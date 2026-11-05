"""Build a release only from a literal committed tree and its explicit exports."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zipfile

COMPONENT = "agent-skills"
REPOSITORY = "gupta-ash/agent-skills"
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
HEX = re.compile(r"[0-9a-f]{40}\Z")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)), *(f"LPT{i}" for i in range(1,10))}

def relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("unsafe package-relative path")
    p = PurePosixPath(value)
    if p.is_absolute() or str(p) != value or any(x in ("", ".", "..") or x.endswith((".", " ")) or x.split(".")[0].upper() in RESERVED for x in value.split("/")):
        raise ValueError("unsafe package-relative path")
    return value

def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False)+"\n").encode("utf-8")

def digest(value):
    return hashlib.sha256(value).hexdigest()

def run_git(root, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "diff.autoRefreshIndex=false", "-C", str(root), *args], env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

def build(source_root, revision, tag, out_dir):
    root = Path(source_root).resolve(strict=True)
    if Path(run_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ValueError("--source-root must be the selected checkout root")
    if not HEX.fullmatch(revision) or not TAG.fullmatch(tag):
        raise ValueError("--revision requires a literal full lowercase commit; --tag requires exact vMAJOR.MINOR.PATCH")
    if run_git(root, "rev-parse", revision+"^{commit}").decode().strip() != revision:
        raise ValueError("revision is not the selected literal commit")
    out = Path(out_dir)
    if not out.is_absolute() or out.exists():
        raise ValueError("--out-dir must be a fresh absolute directory")
    for forbidden in (root, Path.home() / ".agents", Path.home() / ".claude", Path.home() / ".omp"):
        if out.resolve().is_relative_to(forbidden.resolve()) or forbidden.resolve().is_relative_to(out.resolve()):
            raise ValueError("output overlaps source or installed state")
    for parent in (out.parent, *out.parent.parents):
        if parent.exists() and (parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("output crosses a reparse point")
    raw = run_git(root, "archive", "--format=tar", revision)
    committed = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            if member.isdir():
                continue
            name = relative(member.name)
            if not member.isfile() or name.casefold() in {x.casefold() for x in committed}:
                raise ValueError("committed source contains a link or case collision")
            committed[name] = (archive.extractfile(member).read(), "100755" if member.mode & 0o111 else "100644")
    declaration = json.loads(committed["deploy/exports.json"][0])
    if set(declaration) != {"schema_version", "component", "entries"} or declaration["schema_version"] != 1:
        raise ValueError("invalid explicit export declaration")
    files = {}
    destinations = set()
    for row in declaration["entries"]:
        if set(row) != {"source", "destination"}:
            raise ValueError("export rows must contain source and destination only")
        src, dst = relative(row["source"]), relative(row["destination"])
        if not dst.startswith("harness/.") or dst.casefold() in destinations:
            raise ValueError("invalid or duplicate explicit home destination")
        if "/profiles/" in dst:
            raise ValueError("profile duplicates are not release inputs")
        destinations.add(dst.casefold())
        files["payload/"+dst.removeprefix("harness/")] = committed[src]
    for name in ("install.ps1", "deploy/install.py", "deploy/exports.json", "deploy/retired.json"):
        files[name] = committed[name]
    for name in ("LICENSE", "NOTICE"):
        if name in committed:
            files[name] = committed[name]
    prefix = "ash_" if COMPONENT == "skills-private" else ""
    discriminator = "payload/.agents/skills/"+prefix+"discriminator/"
    if sum(p.startswith(discriminator) for p in files) != 27:
        raise ValueError("release must retain the complete 27-file discriminator runtime")
    save = "payload/.agents/skills/"+prefix+"save/scripts/"
    if sum(p.startswith(save) and p.endswith(".py") for p in files) != 7:
        raise ValueError("release must retain all seven current save helpers")
    if COMPONENT == "agent-skills":
        fronts = {x.split("/")[3] for x in files if x.startswith("payload/.agents/skills/") and x.endswith("/SKILL.md")}
        expected = {"discriminator", "obsidian", "save", "restore", "webfetch", "websearch", "wiki", "wiki-compile", "wiki-reverify", "wiki-strategize"}
        if fronts != expected:
            raise ValueError("public release must expose exactly the ten declared fronts")
    rows = [{"path":p, "sha256":digest(b), "mode":m} for p,(b,m) in sorted(files.items())]
    manifest = {"schema_version":1,"component":COMPONENT,"repository":REPOSITORY,"version":tag,"source_commit":revision,"files":rows}
    files["manifest.json"] = (encoded(manifest), "100644")
    out.mkdir(parents=True)
    archive_path = out / f"{COMPONENT}-{tag}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (content, mode) in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(2020,1,1,0,0,0))
            entry.create_system = 3
            entry.external_attr = (0o100000 | int(mode[-3:],8)) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    sha = digest(archive_path.read_bytes())
    receipt = {"schema_version":1,"component":COMPONENT,"repository":REPOSITORY,"version":tag,"source_commit":revision,"archive_sha256":sha,"members":sorted(files)}
    (out / (archive_path.name+".json")).write_bytes(encoded(receipt))
    (out / (archive_path.name+".sha256")).write_text(f"{sha}  {archive_path.name}\n", encoding="ascii")
    (out / "install.ps1").write_bytes(files["install.ps1"][0])
    print(json.dumps({"archive":str(archive_path),"receipt":str(out/(archive_path.name+".json")),"sha256":sha,"source_commit":revision}))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "revision", "tag", "out-dir"):
        p.add_argument("--"+name, required=True)
    a=p.parse_args()
    try:
        build(a.source_root,a.revision,a.tag,a.out_dir)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        p.exit(1, f"Package refused: {exc}\n")

if __name__ == "__main__":
    main()
