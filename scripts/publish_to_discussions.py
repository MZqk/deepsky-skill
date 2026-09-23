#!/usr/bin/env python3
"""Publish a skill announcement to a GitHub repository's Discussions.

No `gh` CLI required — uses GitHub REST + GraphQL over stdlib urllib only.

Prerequisites
-------------
1. The target repo must have Discussions enabled.
   (Web: repo Settings -> General -> Features -> Discussions; or run with --ensure-discussions)
2. A token with write access to Discussions in env GH_TOKEN.
   - Classic PAT: scope `repo`
   - Fine-grained: `Discussions: write`; plus `Administration: write` if using --ensure-discussions

Usage
-----
  # One-shot, write the body inline
  GH_TOKEN=xxx python3 scripts/publish_to_discussions.py \
      --repo MZqk/deepsky-skill --category "Show and tell" \
      --title "deep-sky-capture-advisor 1.0.3 released" --body-file announce.md

  # Auto-generate body + title from a skill dir's SKILL.md frontmatter
  GH_TOKEN=xxx python3 scripts/publish_to_discussions.py \
      --repo MZqk/deepsky-skill --category "Announcements" \
      --body-from-skill deep-sky-capture-advisor --version 1.0.3

  # Ensure Discussions is enabled on the repo first (modifies repo settings)
  GH_TOKEN=xxx python3 scripts/publish_to_discussions.py --repo ... --ensure-discussions
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

REST = "https://api.github.com"
GQL = "https://api.github.com/graphql"


def _api(method, url, token, data=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "publish-to-discussions",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode() or "{}"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def _gql(token, query, variables):
    status, data = _api("POST", GQL, token, {"query": query, "variables": variables})
    if status != 200 or "errors" in data:
        print(f"GraphQL failed (HTTP {status}): {json.dumps(data)[:500]}", file=sys.stderr)
        sys.exit(1)
    return data["data"]


def ensure_discussions(token, repo):
    status, data = _api("PATCH", f"{REST}/repos/{repo}", token, {"has_discussions": True})
    if status not in (200, 201):
        print(f"FAILED to enable Discussions on {repo}: HTTP {status} {json.dumps(data)[:300]}",
              file=sys.stderr)
        sys.exit(1)
    print(f"[ok] Discussions enabled on {repo}")


def get_repo_and_categories(token, repo):
    owner, name = repo.split("/")
    q = """
    query($o:String!, $n:String!) {
      repository(owner:$o, name:$n) {
        id
        discussions(first:50) {
          categories(first:50) { nodes { id name } }
        }
      }
    }"""
    data = _gql(token, q, {"o": owner, "n": name})
    repo_obj = data["repository"]
    if not repo_obj:
        print(f"Repo {repo} not found or token lacks access.", file=sys.stderr)
        sys.exit(1)
    cats = repo_obj["discussions"]["categories"]["nodes"]
    if not cats:
        print("Repo has no Discussion categories. Enable Discussions in repo Settings first "
              "(or rerun with --ensure-discussions, then create a category in the UI).",
              file=sys.stderr)
        sys.exit(1)
    return repo_obj["id"], cats


def create_discussion(token, repo_id, category_id, title, body):
    m = """
    mutation($repoId:ID!, $catId:ID!, $title:String!, $body:String!) {
      createDiscussion(input:{
        repositoryId:$repoId, categoryId:$catId, title:$title, body:$body
      }) {
        discussion { id number url }
      }
    }"""
    data = _gql(token, m, {"repoId": repo_id, "catId": category_id,
                           "title": title, "body": body})
    return data["createDiscussion"]["discussion"]


def parse_frontmatter(path):
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


def build_from_skill(skill_dir, version, repo):
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        print(f"No SKILL.md in {skill_dir}", file=sys.stderr)
        sys.exit(1)
    fm = parse_frontmatter(skill_md)
    name = fm.get("name") or os.path.basename(os.path.abspath(skill_dir))
    desc = fm.get("description", "")
    title = f"{name} {version} released" if version else f"{name} update"
    install = (
        f"ln -s \"$(pwd)/{os.path.basename(os.path.abspath(skill_dir))}\" "
        f"\"${{CODEX_HOME:-$HOME/.codex}}/skills/{os.path.basename(os.path.abspath(skill_dir))}\""
    )
    body = f"""## {name}{' ' + version if version else ''}

{desc}

**Install**
```bash
{install}
```

**Repo:** https://github.com/{repo}

See `README.md` for full usage. Feedback and questions welcome in this discussion.
"""
    return title, body


def main():
    ap = argparse.ArgumentParser(description="Publish a skill post to GitHub Discussions")
    ap.add_argument("--repo", default="MZqk/deepsky-skill")
    ap.add_argument("--category", default="Announcements")
    ap.add_argument("--title")
    ap.add_argument("--body")
    ap.add_argument("--body-file")
    ap.add_argument("--body-from-skill")
    ap.add_argument("--version")
    ap.add_argument("--ensure-discussions", action="store_true",
                    help="Enable Discussions on the repo via API (needs Administration:write)")
    args = ap.parse_args()

    token = os.environ.get("GH_TOKEN")
    if not token:
        print("Set GH_TOKEN in env (classic `repo` scope or fine-grained Discussions:write).",
              file=sys.stderr)
        sys.exit(1)
    if args.repo.count("/") != 1:
        print("--repo must be 'owner/name'", file=sys.stderr)
        sys.exit(1)

    if args.ensure_discussions:
        ensure_discussions(token, args.repo)

    if args.body_from_skill:
        title, body = build_from_skill(args.body_from_skill, args.version, args.repo)
        title = args.title or title
    else:
        if args.body_file:
            body = open(args.body_file, encoding="utf-8").read()
        else:
            body = args.body
        title = args.title
    if not title or not body:
        print("Provide --title and (--body | --body-file | --body-from-skill).", file=sys.stderr)
        sys.exit(1)

    repo_id, cats = get_repo_and_categories(token, args.repo)
    cat = next((c for c in cats if c["name"].lower() == args.category.lower()), None)
    if not cat:
        print(f"Category '{args.category}' not found. Available: "
              f"{[c['name'] for c in cats]}", file=sys.stderr)
        sys.exit(1)

    disc = create_discussion(token, repo_id, cat["id"], title, body)
    print(f"[ok] Posted: {disc['url']}")


if __name__ == "__main__":
    main()
