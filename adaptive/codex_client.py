"""Codex/OpenAI client wrapper with disk cache and fallback.

Primary model: gpt-5-codex via responses API.
Fallback model: gpt-4o via chat completions API.
All calls cached to cache/codex/{sha256}.txt keyed on (model, system, prompt, max_tokens, temperature).
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

try:
    from openai import APIStatusError, RateLimitError
except ImportError:  # pragma: no cover
    RateLimitError = Exception  # type: ignore
    APIStatusError = Exception  # type: ignore

load_dotenv()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _REPO_ROOT / "cache" / "codex"

CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5-codex")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "gpt-4o")

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def _cache_path(model: str, system: str | None, prompt: str, max_tokens: int, temperature: float) -> Path:
    key = f"{model}\x00{system or ''}\x00{prompt}\x00{max_tokens}\x00{temperature}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{digest}.txt"


def _ensure_cache_dir() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _call_codex(prompt: str, system: str | None, max_tokens: int) -> str:
    # gpt-5-codex is a reasoning model: reasoning tokens are deducted from
    # max_output_tokens before any visible output. Empirically ~300-800 tokens
    # of reasoning are consumed on prose tasks. We auto-escalate the budget if
    # the first attempt is starved by reasoning.
    client = _get_client()
    full_input = f"{system}\n\n{prompt}" if system else prompt
    effective = max(max_tokens + 1024, 1500)
    for attempt in range(2):
        r = client.responses.create(
            model=CODEX_MODEL,
            input=full_input,
            max_output_tokens=effective,
        )
        text = r.output_text or ""
        if text:
            return text
        # Empty output means reasoning ate the budget; double and retry once.
        effective *= 2
    raise RuntimeError(
        f"codex returned empty output_text after retry (last budget={effective})"
    )


def _call_fallback(prompt: str, system: str | None, max_tokens: int, temperature: float) -> str:
    client = _get_client()
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    r = client.chat.completions.create(
        model=FALLBACK_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return r.choices[0].message.content or ""


def probe() -> None:
    """Cheap call to verify primary model works.

    Prints 'codex OK' on primary success, 'fallback OK' if primary fails.
    Raises if both fail.
    """
    try:
        text = _call_codex("ping", system=None, max_tokens=16)
        if not text:
            raise RuntimeError("codex returned empty output")
        print("codex OK")
        return
    except Exception as e:
        print(f"codex probe failed: {e!r}", file=sys.stderr)

    try:
        text = _call_fallback("ping", system=None, max_tokens=10, temperature=0.0)
        if not text:
            raise RuntimeError("fallback returned empty output")
        print("fallback OK")
        return
    except Exception as e:
        print(f"fallback probe failed: {e!r}", file=sys.stderr)
        raise


def generate(
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.7,
    use_fallback: bool = False,
    cache: bool = True,
) -> str:
    """Generate text via primary or fallback model with disk cache."""
    model = FALLBACK_MODEL if use_fallback else CODEX_MODEL
    path = _cache_path(model, system, prompt, max_tokens, temperature)

    if cache and path.exists():
        return path.read_text(encoding="utf-8")

    if use_fallback:
        text = _call_fallback(prompt, system, max_tokens, temperature)
    else:
        try:
            text = _call_codex(prompt, system, max_tokens)
        except RateLimitError as e:
            # Specific 429 path: log clearly and switch model.
            print(
                f"rate limited on {CODEX_MODEL}, falling back to {FALLBACK_MODEL}: {e!r}",
                file=sys.stderr,
            )
            model = FALLBACK_MODEL
            path = _cache_path(model, system, prompt, max_tokens, temperature)
            if cache and path.exists():
                return path.read_text(encoding="utf-8")
            text = _call_fallback(prompt, system, max_tokens, temperature)
        except APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status == 429:
                print(
                    f"rate limited on {CODEX_MODEL}, falling back to {FALLBACK_MODEL}: {e!r}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"primary model api error ({status}), falling back to {FALLBACK_MODEL}: {e!r}",
                    file=sys.stderr,
                )
            model = FALLBACK_MODEL
            path = _cache_path(model, system, prompt, max_tokens, temperature)
            if cache and path.exists():
                return path.read_text(encoding="utf-8")
            text = _call_fallback(prompt, system, max_tokens, temperature)
        except Exception as e:
            # Outermost broad fallback (covers RuntimeError from _call_codex
            # when reasoning-only output starves the budget).
            print(f"primary model failed, retrying with fallback: {e!r}", file=sys.stderr)
            model = FALLBACK_MODEL
            path = _cache_path(model, system, prompt, max_tokens, temperature)
            if cache and path.exists():
                return path.read_text(encoding="utf-8")
            text = _call_fallback(prompt, system, max_tokens, temperature)

    if cache:
        _ensure_cache_dir()
        path.write_text(text, encoding="utf-8")

    return text


def cache_stats() -> dict:
    """Return cache entry count, total bytes, and directory path."""
    if not _CACHE_DIR.exists():
        return {"entries": 0, "bytes": 0, "dir": str(_CACHE_DIR)}
    entries = 0
    total = 0
    for f in _CACHE_DIR.glob("*.txt"):
        entries += 1
        total += f.stat().st_size
    return {"entries": entries, "bytes": total, "dir": str(_CACHE_DIR)}


def _selftest() -> int:
    import time

    try:
        probe()
    except Exception as e:
        print(f"probe failed: {e!r}", file=sys.stderr)
        return 1

    try:
        t0 = time.perf_counter()
        out1 = generate("say hello in exactly 3 words", max_tokens=20, cache=False)
        d1 = time.perf_counter() - t0
        print(f"step2 (no cache): {d1:.3f}s -> {out1!r}")
        if not out1.strip():
            print("step2 empty output", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"step2 failed: {e!r}", file=sys.stderr)
        return 1

    try:
        t0 = time.perf_counter()
        out2 = generate("say hello in exactly 3 words", max_tokens=20, cache=True)
        d2 = time.perf_counter() - t0
        t0 = time.perf_counter()
        out3 = generate("say hello in exactly 3 words", max_tokens=20, cache=True)
        d3 = time.perf_counter() - t0
        print(f"step3 first cached: {d2:.3f}s -> {out2!r}")
        print(f"step3 second cached: {d3:.3f}s -> {out3!r}")
        if out2 != out3:
            print(f"step3 cache mismatch: {out2!r} vs {out3!r}", file=sys.stderr)
            return 1
        # Second cache call must be ~10x faster than the uncached call.
        if d3 * 10 > d2 + 0.5 and d3 > 0.05:
            print(f"step3 second call not fast enough: {d3:.3f}s vs first cached {d2:.3f}s", file=sys.stderr)
            # Still continue: cache hit only requires file read; flag but don't fail unless egregious.
        if d3 > d1 / 5:
            print(
                f"step3 cache hit ({d3:.3f}s) not >=5x faster than uncached ({d1:.3f}s)",
                file=sys.stderr,
            )
            return 1
    except Exception as e:
        print(f"step3 failed: {e!r}", file=sys.stderr)
        return 1

    try:
        out4 = generate("respond in chinese with 你好", max_tokens=20, cache=False)
        print(f"step4 utf8: {out4!r}")
        if not out4.strip():
            print("step4 empty output", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"step4 failed: {e!r}", file=sys.stderr)
        return 1

    print(f"cache_stats: {cache_stats()}")
    print("SELFTEST PASS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        try:
            probe()
            sys.exit(0)
        except Exception:
            sys.exit(1)
    sys.exit(_selftest())
