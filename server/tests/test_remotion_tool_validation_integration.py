"""Opt-in Linux renderer integration checks for the PR76 browser test context.

These tests are intentionally skipped unless ``IMV_TEST_RENDERER=1`` because
they require the pinned Chromium binary and bubblewrap sandbox.
"""

import asyncio
import os

import pytest

from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.tool_validation import ToolValidator
from server.remotion_templates.tools.contracts import ComponentDefinition, RenderValidationInput, TestScript


pytestmark = pytest.mark.skipif(os.environ.get("IMV_TEST_RENDERER") != "1", reason="set IMV_TEST_RENDERER=1 for Linux sandbox integration")


CODE = """
import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
type Props = {title: string; nested: {value: string}};
export default function ValidationTemplate(props: Props) {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 15], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <AbsoluteFill data-testid="canvas"><div data-testid="label" style={{opacity}}>{props.title}</div><div data-testid="nested">{props.nested.value}</div></AbsoluteFill>;
}
"""
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "nested": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
    },
    "required": ["title", "nested"],
    "additionalProperties": False,
}


def component():
    """Return the deterministic test component and complete defaults."""
    return ComponentDefinition(code=CODE, parameter_schema=SCHEMA, default_parameters={"title": "default", "nested": {"value": "one"}})


def validator(tmp_path):
    """Construct a Linux renderer validator using only server-owned settings."""
    settings = Settings(_env_file=None, data_dir=tmp_path)
    return ToolValidator(Renderer(settings), tmp_path / "validation")


def test_real_browser_context_checks_frames_queries_and_partial_merge(tmp_path):
    """Seek endpoints, inspect computed style/text, and preserve unpatched defaults."""
    tests = [TestScript(name="timeline", code="""
export default async function run(ctx) {
  await ctx.set_frame(0);
  const first = await ctx.query('[data-testid="label"]');
  ctx.assert(first !== null, 'label exists');
  if (first) {
    ctx.assert_equal(first.text, 'default', 'default text');
    ctx.assert_close(Number(first.styles.opacity), 0, 0.01, 'frame zero');
  }
  await ctx.set_frame(7);
  const middle = await ctx.query('[data-testid="label"]');
  ctx.assert(middle !== null, 'middle label exists');
  if (middle) ctx.assert_close(Number(middle.styles.opacity), 7 / 15, 0.08, 'frame middle');
  await ctx.set_frame(149);
  const last = await ctx.query('[data-testid="label"]');
  ctx.assert(last !== null, 'end label exists');
  if (last) ctx.assert_close(Number(last.styles.opacity), 1, 0.01, 'frame end');
  await ctx.set_parameters({nested: {value: 'changed'}});
  const nested = await ctx.query('[data-testid="nested"]');
  if (nested) ctx.assert_equal(nested.text, 'changed', 'partial object patch');
}
"""), TestScript(name="reset", code="""
export default async function run(ctx) {
  const nested = await ctx.query('[data-testid="nested"]');
  ctx.assert(nested !== null, 'reset element exists');
  if (nested) ctx.assert_equal(nested.text, 'one', 'new script starts from defaults');
}
""")]
    request = RenderValidationInput(component=component(), duration_frames=150, tests=tests)
    report = asyncio.run(validator(tmp_path).validate_render(request))
    assert report.passed is True, report.model_dump()
    assert report.custom_tests_executed == 2


def test_real_browser_context_distinguishes_failed_and_error_scripts(tmp_path):
    """Caught assertion failures remain failed, while zero assertions are errors."""
    tests = [
        TestScript(name="caught_failure", code="""
export default async function run(ctx) {
  try { ctx.assert(false, 'must remain failed'); } catch (_) {}
}
"""),
        TestScript(name="no_assertions", code="""
export default async function run(ctx) { await ctx.set_frame(1); }
"""),
        TestScript(name="out_of_range", code="""
export default async function run(ctx) { await ctx.set_frame(ctx.composition.duration_frames); }
"""),
    ]
    report = asyncio.run(validator(tmp_path).validate_render(RenderValidationInput(component=component(), duration_frames=150, tests=tests)))
    assert report.passed is False
    assert [item.status for item in report.tests] == ["failed", "error", "error"]
