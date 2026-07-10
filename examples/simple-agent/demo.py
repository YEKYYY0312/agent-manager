"""Simple Agent Demo - generates real trace files for Agent DevTools.

This demo implements a tiny "weather Q&A" agent pipeline that records every
step through the SDK. It runs two scenarios:

1. **Success** - planner -> tool -> model call -> answer
2. **Failure** - planner -> tool (times out) -> error

After running, check the trace files:

    py -m agent_devtools_cli.main show traces/simple-agent-success.trace.json
    py -m agent_devtools_cli.main diff traces/simple-agent-success.trace.json traces/simple-agent-failure.trace.json
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make sure the package is importable from the project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "python-sdk"))

from agent_devtools import Cost, Error, TraceContext, traced_model, traced_step, traced_tool


# ---------------------------------------------------------------------------
# "Fake" services that the agent calls
# ---------------------------------------------------------------------------

WEATHER_DB: dict[str, dict[str, str]] = {
    "shanghai": {"summary": "Warm and humid, chance of rain, 28 C"},
    "beijing": {"summary": "Sunny and dry, 22 C"},
    "tokyo": {"summary": "Cloudy with light drizzle, 19 C"},
}


def weather_lookup(city: str) -> dict[str, str]:
    """Simulate a weather API call. Raises TimeoutError for unknown cities."""
    time.sleep(0.05)
    key = city.lower()
    if key not in WEATHER_DB:
        raise TimeoutError(f"weather.lookup timed out for '{city}' after 7s")
    return {"city": city, "summary": WEATHER_DB[key]["summary"]}


# ---------------------------------------------------------------------------
# Traced agent functions (decorator style)
# ---------------------------------------------------------------------------


@traced_step("planner", "Create answer plan", replayable=True)
def plan_answer(question: str) -> str:
    """Decide what to do to answer the user's question."""
    time.sleep(0.01)
    if "weather" in question.lower():
        return "Call weather tool, then summarize result."
    return "Answer directly."


@traced_model("Generate final answer", model="gpt-4.1-mini")
def generate_answer(context: dict[str, str]) -> dict:
    """Simulate an LLM call that produces the final answer."""
    time.sleep(0.03)
    weather = context.get("weather_summary", "No weather data")
    answer = f"The weather is: {weather}"
    # Return a dict that includes a usage field so the decorator extracts cost
    return {
        "content": answer,
        "usage": {"prompt_tokens": 420, "completion_tokens": 36, "total_tokens": 456},
    }


@traced_tool("weather.lookup")
def traced_weather_lookup(city: str) -> dict[str, str]:
    """Traced wrapper around the raw weather lookup."""
    return weather_lookup(city)


# ---------------------------------------------------------------------------
# Success scenario (context manager style, mixed with decorators)
# ---------------------------------------------------------------------------


def run_success_scenario(city: str = "Shanghai") -> None:
    """Record a successful agent run using a mix of context managers and decorators."""
    with TraceContext(
        task=f"Find the current weather summary for {city} and produce a short answer.",
        labels={"scenario": "success", "city": city.lower()},
    ) as ctx:
        # ---- Planner step (decorator) ----
        plan = plan_answer(f"What's the weather in {city}?")

        # ---- Tool call step (context manager) ----
        with ctx.tool_call("weather.lookup", args={"city": city}) as tool_step:
            result = weather_lookup(city)
            tool_step.complete(status="success", output=result)

        # ---- Model call step (decorator, auto-extracts cost) ----
        answer = generate_answer({"weather_summary": result["summary"]})

        # Complete the run
        ctx.trace.run.complete(
            status="success",
            final_output=answer["content"],
        )

    print(f"[OK] Success trace written: traces/{ctx.trace.run.id}.trace.json")


# ---------------------------------------------------------------------------
# Failure scenario (tool timeout)
# ---------------------------------------------------------------------------


def run_failure_scenario(city: str = "UnknownCity") -> None:
    """Record a failing agent run (tool timeout)."""
    with TraceContext(
        task=f"Find the current weather summary for {city} and produce a short answer.",
        labels={"scenario": "failure", "city": city.lower()},
    ) as ctx:
        # ---- Planner step ----
        plan = plan_answer(f"What's the weather in {city}?")

        # ---- Tool call that times out ----
        with ctx.tool_call("weather.lookup", args={"city": city}) as tool_step:
            try:
                result = weather_lookup(city)
            except TimeoutError:
                # Record the failure explicitly in the step
                tool_step.complete(
                    status="timeout",
                    error=Error(type="ToolTimeout", message=f"weather.lookup timed out for '{city}' after 7s"),
                )
                # Mark the whole run as error
                ctx.trace.run.complete(
                    status="error",
                    final_output=None,
                )
                # Let the context manager finish; it will not swallow, but we caught it here.

    print(f"[OK] Failure trace written: traces/{ctx.trace.run.id}.trace.json")


# ---------------------------------------------------------------------------
# Decorator-only variant (all steps via decorators, no context managers)
# ---------------------------------------------------------------------------


def run_decorator_only_scenario() -> None:
    """Same pipeline, but everything recorded via decorators."""
    with TraceContext(
        task="Decorator-only weather query for Tokyo",
        labels={"scenario": "decorator-only"},
    ) as ctx:
        plan = plan_answer("What's the weather in Tokyo?")
        tool_result = traced_weather_lookup("Tokyo")
        answer = generate_answer({"weather_summary": tool_result["summary"]})

        ctx.trace.run.complete(status="success", final_output=answer["content"])

    print(f"[OK] Decorator-only trace written: traces/{ctx.trace.run.id}.trace.json")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("Agent DevTools - Simple Agent Demo\n")
    run_success_scenario("Shanghai")
    run_failure_scenario("UnknownCity")
    run_decorator_only_scenario()
    print("\nDone. Use the CLI to inspect:")
    print("  py -m agent_devtools_cli.main list traces/")
    print("  py -m agent_devtools_cli.main show traces/<file>.trace.json")


if __name__ == "__main__":
    main()
