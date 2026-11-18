"""Docs search over any llms.txt, using TypeSafe's Jev to pick, not embeddings.

Remix of TypeSafe's "semantic find" cookbook: tag items with IDs, ask a Choice
question ("which one answers this?") plus a Noul ("does any of them?").
A Choice takes at most 255 options, so bigger lists run as a tournament:
parallel heats of up to 255, then a final among each heat's best.

The same pick() runs twice: first over the site's pages (from llms.txt),
then over the chunks of the winning pages.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"           # pinned, so thresholds don't shift under us
MAX_OPTIONS = 255               # Choice limit
KEEP_PER_HEAT = 3               # survivors from each heat into the final
USER_AGENT = "token-estimator-demo/0.1"
CHUNK_CHARS = 2_500             # trim very long chunks
MAX_HEAT_CHARS = 80_000         # ~20k tokens: Jev allows 32k per question (state + question)

LINK = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]\(([^)]+)\)(?::\s*(.*))?")
HEADING = re.compile(r"^(#{1,6})\s+(.*)")

stats = {"requests": 0, "input_tokens": 0}


# --- TypeSafe ---

def _api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY")
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not key and os.path.exists(env_file):
        for line in open(env_file):
            if line.strip().startswith("TYPESAFE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        raise RuntimeError("Set TYPESAFE_API_KEY (environment or .env)")
    return key


def jev(state: str, questions: dict) -> dict:
    """One System One request. Raises on failure: a failed call is not a 'no'."""
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(TYPESAFE_URL, data=body, headers={
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"TypeSafe {e.code}: {e.read().decode()[:300]}") from None
    stats["requests"] += 1
    stats["input_tokens"] += data.get("usage", {}).get("input_tokens", 0)
    return data["answers"]


# --- Question wording (kept in one place, per the TypeSafe skill) ---

# Pages are only titles and descriptions, so they can't "answer" anything; we ask
# which one most likely contains the answer. Chunks hold real text, so we ask
# which one answers it.

def where_question(query: str, kind: str, ids: list[str]) -> dict:
    if kind == "page":
        text = f'Which page in `state` most likely contains the answer to: "{query}"?'
    else:
        text = f'Which chunk in `state` best answers: "{query}"?'
    return {"type": "choice", "instructions": text, "criteria": {i: None for i in ids}}


def exists_question(query: str, kind: str) -> dict:
    if kind == "page":
        return {
            "type": "noul",
            "instructions": f'Is any page in `state` likely to cover: "{query}"?',
            "criteria": {
                "true": "A page's title or description is about this topic",
                "false": "No page is about this topic",
            },
        }
    return {
        "type": "noul",
        "instructions": f'Does any chunk in `state` answer: "{query}"?',
        "criteria": {
            "true": "At least one chunk states or directly covers the answer",
            "false": "No chunk addresses this",
        },
    }


# --- The tournament ---

def _round(query: str, kind: str, labelled: list[tuple[int, str]]) -> tuple[list, float]:
    """One Choice + Noul over up to 255 items. Returns ([(index, prob)], exists)."""
    ids = [f"{kind[0].upper()}{n:03d}" for n in range(len(labelled))]
    state = "\n".join(f"{i}| {text}" for i, (_, text) in zip(ids, labelled))
    answers = jev(state, {
        "where": where_question(query, kind, ids),
        "exists": exists_question(query, kind),
    })
    probs = answers["where"]["probabilities"]
    ranked = sorted(
        ((labelled[n][0], probs.get(i, 0.0)) for n, i in enumerate(ids)),
        key=lambda x: x[1], reverse=True,
    )
    return ranked, answers["exists"]["noul"]


def _heats(labelled: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Split into heats that fit both the 255-option limit and the context limit."""
    heats, current, size = [], [], 0
    for item in labelled:
        if current and (len(current) == MAX_OPTIONS or size + len(item[1]) > MAX_HEAT_CHARS):
            heats.append(current)
            current, size = [], 0
        current.append(item)
        size += len(item[1])
    return heats + [current] if current else heats


def pick(query: str, kind: str, items: list[str]) -> tuple[list, float]:
    """Rank items for the query. Heats in parallel, then a final among the best."""
    labelled = list(enumerate(items))
    heats = _heats(labelled)
    if len(heats) == 1:
        return _round(query, kind, labelled)
    with ThreadPoolExecutor(len(heats)) as pool:
        results = list(pool.map(lambda h: _round(query, kind, h)[0], heats))
    finalists = [idx for ranked in results for idx, _ in ranked[:KEEP_PER_HEAT]]
    return _round(query, kind, [(i, items[i]) for i in finalists])


# --- Fetching and splitting ---

# Recently fetched documents: {url: (fetched_at, text)}. Lives in the app's memory,
# so it's per container and empties when Modal stops an idle one. Docs change
# rarely, so serving a copy up to an hour old is fine.
_cache: dict[str, tuple[float, str]] = {}
CACHE_SECONDS = 3600
MAX_DOC_BYTES = 2_000_000  # refuse anything bigger than a docs page should be


def fetch(url: str) -> str:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        if "html" in r.headers.get("Content-Type", ""):
            raise ValueError(f"{url} is HTML, not Markdown")
        body = r.read(MAX_DOC_BYTES + 1)
    if len(body) > MAX_DOC_BYTES:
        raise ValueError(f"{url} is too large")
    text = body.decode()
    _cache[url] = (time.time(), text)
    return text


def same_site(url: str, index_url: str) -> bool:
    """Only follow links on the index's own site, over HTTPS. Stops an index from
    pointing our server at other machines (server-side request forgery)."""
    link, index = urlparse(url), urlparse(index_url)
    return link.scheme == "https" and link.hostname == index.hostname


def parse_llms_txt(text: str, index_url: str) -> list[dict]:
    pages = []
    for line in text.splitlines():
        m = LINK.match(line)
        if m:
            url = urljoin(index_url, m.group(2))  # relative links resolve against the index
            if same_site(url, index_url):
                pages.append({"title": m.group(1), "url": url, "desc": m.group(3) or ""})
    return pages


def is_boilerplate(text: str) -> bool:
    """Navigation notes many docs pages start with, e.g. '> ## Documentation Index'
    or '<!-- llms.txt: ... -->'. They match every query and answer none."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return all(l.startswith(("<!--", ">")) for l in lines) or len(text) < 40


def split_markdown(md: str) -> list[str]:
    """Chunks at headings, each prefixed with its heading path for context."""
    chunks, path, body = [], [], []

    def flush():
        text = "\n".join(body).strip()
        if text and not is_boilerplate(text):
            chunks.append((" > ".join(path) + "\n" + text)[:CHUNK_CHARS])

    for line in md.splitlines():
        m = HEADING.match(line)
        if m:
            flush()
            body = []
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
        else:
            body.append(line)
    flush()
    return chunks


# --- Search ---

ANSWERED, ABSENT = 0.7, 0.35  # the cookbook's starting thresholds for the Noul


def verdict(score: float) -> str:
    if score >= ANSWERED:
        return "answered"
    return "not_found" if score < ABSENT else "partial"


def _page_chunks(page: dict) -> list[str]:
    try:
        return split_markdown(fetch(page["url"]))
    except (ValueError, urllib.error.URLError, UnicodeDecodeError):
        return []  # skip pages we can't read as Markdown


def search(query: str, llms_url: str, pages_to_read: int = 5, top: int = 3) -> dict:
    start = time.time()
    pages = parse_llms_txt(fetch(llms_url), llms_url)
    page_labels = [f"{p['title']}: {p['desc']} ({p['url']})" for p in pages]
    page_rank, _ = pick(query, "page", page_labels)

    # Fetch the top pages in parallel.
    top_pages = [i for i, _ in page_rank[:pages_to_read]]
    with ThreadPoolExecutor(len(top_pages)) as pool:
        per_page = list(pool.map(lambda i: _page_chunks(pages[i]), top_pages))
    chunks = [(i, c) for i, cs in zip(top_pages, per_page) for c in cs]  # (page, text)

    if not chunks:
        score, ranked = 0.0, []
    else:
        ranked, score = pick(query, "chunk", [c for _, c in chunks])
    result = verdict(score)
    return {
        "question": query,
        # When the docs don't seem to answer it, say so first and return nothing
        # to mislead with: agents tend to trust whatever text they're handed.
        "verdict": result,
        "score": round(score, 2),
        "results": [] if result == "not_found" else [
            {"url": pages[chunks[i][0]]["url"], "relevance": round(p, 2), "text": chunks[i][1]}
            for i, p in ranked[:top]
        ],
        "pages_read": [pages[i]["url"] for i in top_pages],
        "seconds": round(time.time() - start, 1),
    }
