"""Run a Transient flow study from a solved steady flow study, read probes, and solve heat on the last state.

Use this when a steady flow solve does not converge. If every probe settles, the steady flow exists and the
steady solver was in a cycle. If the probes keep moving, the flow is unsteady.

The model must have a solved steady flow study (default std1) and a heat study (default std2) that does not
solve the flow. The script:
1. adds a Transient flow study (default std3) that starts from the last state of std1,
2. solves it, and writes u at each probe point against time to a CSV file,
3. points std2 at the last state of std3, solves it, and saves the model.

The calls go through FastMCP.call_tool, the same path that an MCP client uses.

Run (from the repository root, with a COMSOL mphserver on the port):
    python examples/transient_from_steady.py solved.mph out.mph --t-end 0.06 \
        --point 0.02,0.001,0.0125 --point 0.04,0.001,0.0125 --flow-only /physics/ht /multiphysics/noni1
"""
import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.server import mcp, register_all_tools  # noqa: E402

register_all_tools()
_loop = asyncio.new_event_loop()


def call(tool: str, **args) -> dict:
    """Call one MCP tool. Raise on a failed result."""
    out = _loop.run_until_complete(mcp.call_tool(tool, args))
    content = out[0] if isinstance(out, tuple) else out
    result = json.loads(content[0].text if content else "{}")
    if result.get("success", True) is False or "error" in result:
        raise RuntimeError(f"{tool} failed: {json.dumps(result)[:800]}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("solved", help="model with a solved steady flow study")
    parser.add_argument("out", help="where to save the model after the runs")
    parser.add_argument("--port", type=int, default=2036)
    parser.add_argument("--steady", default="std1", help="solved steady flow study")
    parser.add_argument("--transient", default="std3", help="Transient study to add")
    parser.add_argument("--heat", default="std2", help="heat study to solve on the last state. Empty: skip.")
    parser.add_argument("--t-end", type=float, default=0.005, help="end time, s")
    parser.add_argument("--dt-out", type=float, default=2.5e-4, help="output step, s. The solver picks its own steps.")
    parser.add_argument("--rtol", default="1e-3")
    parser.add_argument("--flow-only", nargs="*", default=[], help="physics paths that the Transient study does not solve")
    parser.add_argument("--point", action="append", default=[], help="probe point x,y,z in model units")
    parser.add_argument("--csv", default="probes.csv")
    args = parser.parse_args()
    points = [[float(c) for c in p.split(",")] for p in args.point]

    call("comsol_connect", port=args.port)
    try:
        name = call("model_load", file_path=args.solved)["model"]["name"]
        setup = call("study_transient_from_solution", study_tag=args.transient, from_study=args.steady,
                     tlist=f"range(0,{args.dt_out:g},{args.t_end:g})", rtol=args.rtol,
                     solve_for={path: False for path in args.flow_only})
        print("TRANSIENT", setup["solution"], setup["solver_settings"], setup.get("warning", ""), flush=True)
        # MPh solves a study by its label, for example "Study 3".
        call("study_solve", study_name=setup["label"], timeout=24 * 3600)

        if points:
            probes = call("results_probe_time_series", points=points, expressions="u", study_tag=args.transient)
            with open(args.csv, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["t_s"] + [f"u_{p['name']}" for p in probes["probes"]])
                for i, t in enumerate(probes["times"]):
                    writer.writerow([f"{t:.6g}"] + [f"{p['values']['u'][i]:.6g}" for p in probes["probes"]])
            for p in probes["probes"]:
                u = p["values"]["u"]
                print(f"PROBE {p['name']} u from {u[0]:.5g} to {u[-1]:.5g} ({probes['n_times']} times)", flush=True)

        if args.heat:
            heat = call("study_use_solution", study_tag=args.heat, from_study=args.transient)
            call("study_solve", study_name=heat["label"], timeout=24 * 3600)
            print("HEAT solved on the last state of", args.transient, flush=True)
        call("model_save", file_path=args.out)
        print("SAVED", args.out, flush=True)
        call("model_remove", model_name=name)
    finally:
        call("comsol_disconnect")


if __name__ == "__main__":
    main()
