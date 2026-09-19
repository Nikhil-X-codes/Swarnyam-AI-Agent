"""Standalone Phase 1 smoke-test CLI."""

import argparse

from tools.llm import LLMInputError, call_llm


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one prompt through the Groq LLM wrapper.")
    parser.add_argument("prompt", nargs="?", default="Reply with exactly: Phase 1 is working.")
    args = parser.parse_args()
    try:
        response, usage = call_llm(args.prompt)
        print(response)
        print(f"usage: {usage.input_tokens} in / {usage.output_tokens} out; "
              f"estimated_cost_usd={usage.estimated_cost_usd:.8f}; latency_ms={usage.latency_ms:.2f}")
        return 0
    except LLMInputError as err:
        print(f"Error caught (expected on bad input): {err}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
