"""Unit tests for app.learning.reflect — the traced model call and its
should_record gate. Every test here stubs the Anthropic client; the ONLY
test that proves a real model can fill Lesson is test_learning_live.py.
"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.db.models import RunStatus, RunTrigger
