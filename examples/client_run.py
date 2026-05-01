import asyncio
import json
from modular_agent_designer import load_workflow, build_workflow, run_workflow_async

async def main():
    # 1. Load the workflow configuration from a YAML file
    # Ensure you have 'examples/workflows/hello_world.yaml' or similar in your path
    yaml_path = "examples/workflows/hello_world.yaml"
    
    # 2. Define the input data for the workflow
    input_data = {"topic": "Deep Sea Explorers"}
    
    print(f"--- Loading Workflow: {yaml_path} ---")
    cfg = load_workflow(yaml_path)
    
    print("--- Building Workflow Graph ---")
    workflow = build_workflow(cfg)
    
    print("--- Running Workflow ---")
    # run_workflow_async handles session management and execution
    final_state = await run_workflow_async(workflow, input_data)
    
    print("--- Execution Completed ---")
    print("Final State:")
    print(json.dumps(final_state, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
