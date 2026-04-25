"""Resolve skill configs into ADK SkillToolset instances.

Skill refs use dotted notation pointing to a skill directory::

    modular_agent_designer.skills.summarize-text   # internal (shipped)
    skills.summarize-text                          # local project folder

Resolution algorithm:
1. Split the ref at the last ``.`` → (module_path, skill_dir_name)
2. Import the module_path as a Python package
3. Find ``skill_dir_name/SKILL.md`` in the package's directory
4. Load via ADK's ``load_skill_from_dir``
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from google.adk.skills import Skill, load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from ..config.schema import SkillConfig


def resolve_skill(name: str, cfg: SkillConfig) -> list[Skill]:
    """Resolve a dotted skill ref into a list containing one Skill.

    The ref is split at the last dot:
    - Left part is a Python package path (imported to locate its directory)
    - Right part is a subdirectory name containing ``SKILL.md``

    Example: ``modular_agent_designer.skills.summarize-text``
    → imports ``modular_agent_designer.skills``
    → loads ``<package_dir>/summarize-text/SKILL.md``
    """
    module_path, sep, skill_dir_name = cfg.ref.rpartition(".")
    if not sep:
        raise ValueError(
            f"Skill '{name}': ref '{cfg.ref}' must be a dotted path "
            f"like 'package.skill-name'."
        )

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(
            f"Skill '{name}': could not import module '{module_path}' "
            f"from ref '{cfg.ref}'."
        ) from exc

    if not hasattr(module, "__file__") or module.__file__ is None:
        raise ValueError(
            f"Skill '{name}': module '{module_path}' has no __file__; "
            f"cannot locate skill directory."
        )

    package_dir = Path(module.__file__).parent
    skill_dir = package_dir / skill_dir_name

    if not skill_dir.is_dir():
        raise FileNotFoundError(
            f"Skill '{name}': directory '{skill_dir}' not found "
            f"(from ref '{cfg.ref}')."
        )
    if not (skill_dir / "SKILL.md").exists():
        raise FileNotFoundError(
            f"Skill '{name}': no SKILL.md in '{skill_dir}' "
            f"(from ref '{cfg.ref}')."
        )

    return [load_skill_from_dir(skill_dir)]


def build_skill_registry(
    skills: dict[str, SkillConfig],
) -> dict[str, list[Skill]]:
    """Resolve all skill entries from the YAML skills section."""
    return {name: resolve_skill(name, cfg) for name, cfg in skills.items()}


def build_skill_toolset(
    skill_names: list[str],
    skill_registry: dict[str, list[Skill]],
) -> SkillToolset | None:
    """Build a SkillToolset from named skill aliases, or return None if empty.

    Args:
        skill_names: Alias names as declared on the agent's ``skills`` list.
        skill_registry: Pre-resolved registry mapping alias → list[Skill].

    Returns:
        A ``SkillToolset`` wrapping all resolved skills, or ``None`` when
        the agent declares no skills.
    """
    all_skills: list[Skill] = []
    for name in skill_names:
        all_skills.extend(skill_registry[name])
    if not all_skills:
        return None
    return SkillToolset(skills=all_skills)
