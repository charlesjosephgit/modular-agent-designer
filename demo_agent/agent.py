from pathlib import Path
from modular_agent_designer import load_workflow, build_workflow

yaml_path = Path(__file__).parent / "demo_agent.yaml"
cfg = load_workflow(str(yaml_path))
root_agent = build_workflow(cfg)
