"""Unit tests for Phase 1 Capability Registry and Governance."""

import pytest
from app.core.capabilities import Capability, CapabilityRegistry, get_capability_registry


def test_registry_initialization():
    registry = get_capability_registry()
    names = registry.list_names()
    assert "inspect_file" in names
    assert "search_web" in names
    assert "search_knowledge_base" in names
    assert "generate_docx" in names
    assert "generate_pptx" in names
    assert "generate_xlsx" in names
    assert "validate_artifact" in names
    assert "create_version" in names


def test_custom_capability_registration():
    custom_reg = CapabilityRegistry()
    cap = Capability(
        name="custom_task",
        description="A custom task for testing",
        prerequisites=["inspect_file"],
        supported_artifact_types=["DOCX"],
    )
    custom_reg.register(cap)
    assert custom_reg.get("custom_task") == cap


def test_validate_plan_capabilities_valid():
    registry = get_capability_registry()
    caps = ["inspect_file", "search_web", "generate_pptx", "validate_artifact", "create_version"]
    is_valid, errors = registry.validate_plan_capabilities(caps, artifact_types=["PPTX"])
    assert is_valid is True
    assert len(errors) == 0


def test_validate_plan_capabilities_unknown_capability():
    registry = get_capability_registry()
    caps = ["inspect_file", "non_existent_capability_123"]
    is_valid, errors = registry.validate_plan_capabilities(caps, artifact_types=["DOCX"])
    assert is_valid is False
    assert any("Unknown capability" in err for err in errors)


def test_validate_plan_capabilities_unsupported_artifact_type():
    registry = get_capability_registry()
    # generate_pptx only supports PPTX, testing with XLSX
    caps = ["generate_pptx"]
    is_valid, errors = registry.validate_plan_capabilities(caps, artifact_types=["XLSX"])
    assert is_valid is False
    assert any("does not support requested artifact types" in err for err in errors)


def test_validate_plan_capabilities_prerequisite_failure():
    registry = get_capability_registry()
    # create_version requires validate_artifact
    caps = ["create_version"]
    is_valid, errors = registry.validate_plan_capabilities(caps, artifact_types=["PPTX"])
    assert is_valid is False
    assert any("requires prerequisite 'validate_artifact'" in err for err in errors)
