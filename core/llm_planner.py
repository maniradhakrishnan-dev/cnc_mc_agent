#!/usr/bin/env python3
"""
Step 2: LLM Machining Strategy Planner (Gemini)
features.json + tool_library.json -> strategies.json

The LLM reasons over the geometry and tool physics to propose 3 Pareto Frontier strategies:
  1. CYCLE_TIME    : Fastest material removal, max stepover (75%), aggressive feeds.
  2. ACCURACY_TUNED: Aerospace precision, fine stepover (20%), shallow finishing + spring pass.
  3. BALANCED      : Standard shop production (roughing + clean contour pass).
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.error

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

def load_dotenv():
    for candidate in [os.path.join(AGENT_DIR, ".env"), os.path.join(os.getcwd(), ".env")]:
        if os.path.isfile(candidate):
            with open(candidate, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v

load_dotenv()

SYSTEM_PROMPT = """You are an expert CNC Manufacturing Engineer and CAM Programmer.
Your job is to read CAD geometric features and a tool library, and generate three distinct, verified machining strategies on the Pareto Frontier for Aluminum 6061 milling:

1. 'CYCLE_TIME' (Aggressive / Fastest)
   - Minimize machining time.
   - Use the largest allowable roughing tool.
   - Stepover: 65-75% of tool diameter.
   - Stepdown: Aggressive (up to 1x tool diameter or max flute capacity).
   - Higher feedrates within tool chip-load limits.
   - Single roughing pass with minimal finish allowance.

2. 'ACCURACY_TUNED' (Aerospace Tolerance / Best Finish)
   - Maximize surface precision and minimize scallop height / dimensional error.
   - Stepover: Fine (15-25% of tool diameter).
   - Stepdown: Shallow (1.0 - 1.5mm) to minimize tool deflection.
   - Conservative feedrates.
   - Dedicated finishing passes + 1 spring pass on critical pocket walls.

3. 'BALANCED' (Standard Shop Production)
   - Optimal compromise between cycle time and surface finish.
   - High-speed roughing pass (45-50% stepover) + single finishing contour pass.
   - Standard recommended feeds and speeds.

IMPORTANT CONSTRAINTS & MANDATORY RULES:
- 100% of all extracted features MUST be completely machined across ALL THREE strategies. Never omit a feature.
- Different strategies trade off feeds, speeds, stepovers, and finish passes, but all CAD geometry must be cut.
- If any pocket or slot width <= roughing tool diameter (e.g. min_cavity_width_mm <= tool diameter), you MUST either:
  (a) Choose a primary tool that fits the cavity (diameter < min_cavity_width_mm), OR
  (b) Enable secondary pocket_finishing / rest-machining with a smaller tool that fits (diameter < min_cavity_width_mm) across ALL strategies, including CYCLE_TIME.
- Do not exceed tool flute length or shank reach.
- Pocket internal corners must be finished by a tool with diameter <= max_tool_diameter_mm.
- Output MUST be valid JSON only, conforming exactly to the requested schema.
"""

def build_deterministic_fallback(features, tools, feedback=None):
    """
    Scientifically calculated deterministic strategies for Aluminum 6061,
    used when running offline or without an active API key.
    If feedback from previous iteration is provided, applies closed-loop corrections.
    """
    pockets = features.get("features", {}).get("setup_1_top_3axis", {}).get("pockets") or \
              features.get("features", {}).get("primary_setup_top_3axis", {}).get("pockets") or \
              features.get("features", {}).get("pockets", [])
              
    holes = features.get("features", {}).get("setup_1_top_3axis", {}).get("vertical_holes") or \
            features.get("features", {}).get("primary_setup_top_3axis", {}).get("holes") or \
            features.get("features", {}).get("holes", [])

    min_cavity = features.get("machinability_constraints", {}).get("min_cavity_width_mm")
    if min_cavity is None:
        widths = []
        for p in pockets:
            if p.get("is_slot") and p.get("slot_info"):
                widths.append(p["slot_info"].get("slot_width_mm", 10.0))
            else:
                b = p.get("bounds", {})
                widths.append(min(b.get("width_x_mm", 100.0), b.get("length_y_mm", 100.0)))
        min_cavity = min(widths) if widths else 50.0

    # Pick best tools from library
    deepest = features.get("machinability_constraints", {}).get("deepest_feature_depth_mm", 0.0)
    if deepest > 30.0:
        endmill_rough = next((t for t in tools["tools"] if t["tool_number"] == 11), tools["tools"][0])
        if min_cavity < 6.0:
            endmill_finish = next((t for t in tools["tools"] if t["tool_number"] == 3), tools["tools"][1])
        else:
            endmill_finish = next((t for t in tools["tools"] if t["tool_number"] == 12), tools["tools"][0])
    else:
        endmill_rough = next((t for t in tools["tools"] if t["tool_number"] == 1), tools["tools"][0])
        if min_cavity < 6.0:
            endmill_finish = next((t for t in tools["tools"] if t["tool_number"] == 3), tools["tools"][1])
        else:
            endmill_finish = next((t for t in tools["tools"] if t["tool_number"] == 2), tools["tools"][1])
    # Primary default drill assignment; toolpath_generator automatically handles per-hole diameter matching
    drill_tool = next((t for t in tools["tools"] if t["type"] == "drill"), tools["tools"][0])

    cycle_time_needs_secondary = (min_cavity <= endmill_rough["diameter_mm"])

    base_plan = {
        "part_file": features.get("source_cad_file", "unknown"),
        "material": "Aluminum 6061-T6",
        "strategies": {
            "CYCLE_TIME": {
                "name": "Cycle Time Optimized",
                "description": "High-MRR roughing with minimal run-time while strictly machining 100% of all features.",
                "target_tradeoff": "Fastest cycle time, coarser surface scallops (~35-50 microns)",
                "tool_assignments": {
                    "pocket_roughing": endmill_rough["tool_number"],
                    "pocket_finishing": endmill_finish["tool_number"] if cycle_time_needs_secondary else endmill_rough["tool_number"],
                    "drilling": drill_tool["tool_number"]
                },
                "parameters": {
                    "pocket_roughing": {
                        "spindle_rpm": 8000,
                        "feedrate_mm_min": 1800,
                        "stepover_pct": 75,
                        "stepdown_mm": 4.0,
                        "ramp_plunge_angle_deg": 3.0,
                        "finish_allowance_mm": 0.1 if cycle_time_needs_secondary else 0.0
                    },
                    "pocket_finishing": {
                        "enabled": cycle_time_needs_secondary,
                        "spindle_rpm": 10000,
                        "feedrate_mm_min": 1200,
                        "stepover_pct": 50,
                        "stepdown_mm": 3.0,
                        "spring_passes": 0
                    },
                    "drilling": {
                        "spindle_rpm": 3500,
                        "feedrate_mm_min": 450,
                        "peck_depth_mm": 5.0
                    }
                },
                "predictions": {
                    "predicted_cycle_time_sec": 600.0,
                    "predicted_cycle_time_formatted": "10m 00s",
                    "predicted_mean_deviation_um": 200.0,
                    "predicted_max_scallop_um": 600.0,
                    "predicted_max_chipload_mm": 0.08
                }
            },
            "ACCURACY_TUNED": {
                "name": "Accuracy & Tolerance Tuned",
                "description": "Fine 20% stepover with dedicated finishing pass and spring pass for aerospace tolerances.",
                "target_tradeoff": "Sub-5 micron surface precision, longer cycle time",
                "tool_assignments": {
                    "pocket_roughing": endmill_finish["tool_number"],
                    "pocket_finishing": endmill_finish["tool_number"],
                    "drilling": drill_tool["tool_number"]
                },
                "parameters": {
                    "pocket_roughing": {
                        "spindle_rpm": 9000,
                        "feedrate_mm_min": 900,
                        "stepover_pct": 40,
                        "stepdown_mm": 1.5,
                        "ramp_plunge_angle_deg": 1.5,
                        "finish_allowance_mm": 0.25
                    },
                    "pocket_finishing": {
                        "enabled": True,
                        "spindle_rpm": 10000,
                        "feedrate_mm_min": 600,
                        "stepover_pct": 20,
                        "stepdown_mm": 1.0,
                        "spring_passes": 1
                    },
                    "drilling": {
                        "spindle_rpm": 3000,
                        "feedrate_mm_min": 300,
                        "peck_depth_mm": 2.0
                    }
                },
                "predictions": {
                    "predicted_cycle_time_sec": 3600.0,
                    "predicted_cycle_time_formatted": "60m 00s",
                    "predicted_mean_deviation_um": 10.0,
                    "predicted_max_scallop_um": 30.0,
                    "predicted_max_chipload_mm": 0.04
                }
            },
            "BALANCED": {
                "name": "Balanced Production",
                "description": "Standard high-speed roughing pass followed by a clean contour finishing pass.",
                "target_tradeoff": "Excellent production tolerance (~10-15 microns) at economical cycle time",
                "tool_assignments": {
                    "pocket_roughing": endmill_rough["tool_number"],
                    "pocket_finishing": endmill_finish["tool_number"],
                    "drilling": drill_tool["tool_number"]
                },
                "parameters": {
                    "pocket_roughing": {
                        "spindle_rpm": 8000,
                        "feedrate_mm_min": 1400,
                        "stepover_pct": 50,
                        "stepdown_mm": 2.5,
                        "ramp_plunge_angle_deg": 2.5,
                        "finish_allowance_mm": 0.20
                    },
                    "pocket_finishing": {
                        "enabled": True,
                        "spindle_rpm": 9500,
                        "feedrate_mm_min": 850,
                        "stepover_pct": 35,
                        "stepdown_mm": 2.0,
                        "spring_passes": 0
                    },
                    "drilling": {
                        "spindle_rpm": 3200,
                        "feedrate_mm_min": 400,
                        "peck_depth_mm": 3.0
                    }
                },
                "predictions": {
                    "predicted_cycle_time_sec": 1200.0,
                    "predicted_cycle_time_formatted": "20m 00s",
                    "predicted_mean_deviation_um": 40.0,
                    "predicted_max_scallop_um": 70.0,
                    "predicted_max_chipload_mm": 0.06
                }
            }
        }
    }

    # Closed-loop corrective adjustments from diagnostic critique
    if feedback and "strategies" in feedback:
        for strat_key, strat_critique in feedback["strategies"].items():
            if strat_key in base_plan["strategies"]:
                adj = strat_critique.get("adjustments", {})
                p = base_plan["strategies"][strat_key]["parameters"]
                ta = base_plan["strategies"][strat_key]["tool_assignments"]
                
                violations = [v.get("type", "") for v in strat_critique.get("violations", [])]
                if "UNCUT_MATERIAL" in violations or "PART_GOUGE" in violations or not strat_critique.get("safety_pass", True):
                    p["pocket_finishing"]["enabled"] = True
                    ta["pocket_finishing"] = endmill_finish["tool_number"]
                    if "finish_allowance_mm" in p["pocket_roughing"]:
                        p["pocket_roughing"]["finish_allowance_mm"] = 0.15

                if "finish_stepover_pct" in adj and "pocket_finishing" in p:
                    p["pocket_finishing"]["stepover_pct"] = max(5.0, adj["finish_stepover_pct"])
                    if not p["pocket_finishing"].get("enabled"):
                        p["pocket_finishing"]["enabled"] = True
                
                if "reduce_feed_pct" in adj and "pocket_roughing" in p:
                    red_ratio = min(0.6, adj["reduce_feed_pct"] / 100.0)
                    p["pocket_roughing"]["feedrate_mm_min"] = int(p["pocket_roughing"]["feedrate_mm_min"] * (1.0 - red_ratio))
                
                if "recommended_finishing_tool" in adj:
                    ta["pocket_finishing"] = adj["recommended_finishing_tool"]

    return base_plan


def call_gemini_api(api_key, features, tools, model="gemini-2.5-flash"):
    """
    Calls Google Gemini REST API directly via urllib.
    """
SCHEMA_TEMPLATE = '''{
  "part_file": "part.step",
  "material": "Aluminum 6061-T6",
  "strategies": {
    "CYCLE_TIME": {
      "name": "Cycle Time Optimized",
      "description": "High-MRR roughing prioritizing minimal cycle time.",
      "target_tradeoff": "Fastest cycle time, coarser surface scallops (~35-50 microns)",
      "tool_assignments": {
        "pocket_roughing": 1,
        "pocket_finishing": 1,
        "drilling": 4
      },
      "parameters": {
        "pocket_roughing": {
          "spindle_rpm": 8000,
          "feedrate_mm_min": 1800,
          "stepover_pct": 75,
          "stepdown_mm": 4.0,
          "finish_allowance_mm": 0.0
        },
        "pocket_finishing": {
          "enabled": false,
          "spring_passes": 0
        },
        "drilling": {
          "spindle_rpm": 3500,
          "feedrate_mm_min": 450,
          "peck_depth_mm": 5.0
        }
      },
      "predictions": {
        "predicted_cycle_time_sec": 600.0,
        "predicted_cycle_time_formatted": "10m 00s",
        "predicted_mean_deviation_um": 200.0,
        "predicted_max_scallop_um": 600.0,
        "predicted_max_chipload_mm": 0.08
      }
    },
    "ACCURACY_TUNED": {
      "name": "Accuracy & Tolerance Tuned",
      "description": "Fine stepover and dedicated finishing passes for tight tolerances.",
      "target_tradeoff": "Sub-5 micron surface precision, longer cycle time",
      "tool_assignments": {
        "pocket_roughing": 2,
        "pocket_finishing": 2,
        "drilling": 4
      },
      "parameters": {
        "pocket_roughing": {
          "spindle_rpm": 9000,
          "feedrate_mm_min": 900,
          "stepover_pct": 35,
          "stepdown_mm": 1.5,
          "finish_allowance_mm": 0.25
        },
        "pocket_finishing": {
          "enabled": true,
          "spindle_rpm": 10000,
          "feedrate_mm_min": 600,
          "stepover_pct": 20,
          "stepdown_mm": 1.0,
          "spring_passes": 1
        },
        "drilling": {
          "spindle_rpm": 3500,
          "feedrate_mm_min": 450,
          "peck_depth_mm": 3.0
        }
      },
      "predictions": {
        "predicted_cycle_time_sec": 3600.0,
        "predicted_cycle_time_formatted": "60m 00s",
        "predicted_mean_deviation_um": 10.0,
        "predicted_max_scallop_um": 30.0,
        "predicted_max_chipload_mm": 0.04
      }
    },
    "BALANCED": {
      "name": "Balanced Production",
      "description": "Production standard compromise between time and finish.",
      "target_tradeoff": "Excellent production tolerance (~10-15 microns) at economical cycle time",
      "tool_assignments": {
        "pocket_roughing": 1,
        "pocket_finishing": 2,
        "drilling": 4
      },
      "parameters": {
        "pocket_roughing": {
          "spindle_rpm": 8000,
          "feedrate_mm_min": 1400,
          "stepover_pct": 50,
          "stepdown_mm": 2.5,
          "finish_allowance_mm": 0.2
        },
        "pocket_finishing": {
          "enabled": true,
          "spindle_rpm": 9500,
          "feedrate_mm_min": 850,
          "stepover_pct": 35,
          "stepdown_mm": 2.0,
          "spring_passes": 0
        },
        "drilling": {
          "spindle_rpm": 3500,
          "feedrate_mm_min": 450,
          "peck_depth_mm": 4.0
        }
      },
      "predictions": {
        "predicted_cycle_time_sec": 1200.0,
        "predicted_cycle_time_formatted": "20m 00s",
        "predicted_mean_deviation_um": 40.0,
        "predicted_max_scallop_um": 70.0,
        "predicted_max_chipload_mm": 0.06
      }
    }
  }
}'''

def normalize_strategies(data, features, tools, feedback=None):
    fallback = build_deterministic_fallback(features, tools, feedback=feedback)
    if not isinstance(data, dict) or "strategies" not in data:
        return fallback

    norm = {
        "part_file": data.get("part_file", features.get("source_cad_file", "unknown")),
        "material": data.get("material", "Aluminum 6061-T6"),
        "strategies": {}
    }

    for key in ["CYCLE_TIME", "ACCURACY_TUNED", "BALANCED"]:
        fb_strat = fallback["strategies"][key]
        raw = data.get("strategies", {}).get(key, {})
        raw_pred = raw.get("predictions", {})
        fb_pred = fb_strat.get("predictions", {})
        predictions = {
            "predicted_cycle_time_sec": float(raw_pred.get("predicted_cycle_time_sec", fb_pred.get("predicted_cycle_time_sec", 1200.0))),
            "predicted_cycle_time_formatted": str(raw_pred.get("predicted_cycle_time_formatted", fb_pred.get("predicted_cycle_time_formatted", "20m 00s"))),
            "predicted_mean_deviation_um": float(raw_pred.get("predicted_mean_deviation_um", fb_pred.get("predicted_mean_deviation_um", 50.0))),
            "predicted_max_scallop_um": float(raw_pred.get("predicted_max_scallop_um", fb_pred.get("predicted_max_scallop_um", 80.0))),
            "predicted_max_chipload_mm": float(raw_pred.get("predicted_max_chipload_mm", fb_pred.get("predicted_max_chipload_mm", 0.06)))
        }

        norm["strategies"][key] = {
            "name": raw.get("name", fb_strat["name"]),
            "description": raw.get("description", fb_strat["description"]),
            "target_tradeoff": raw.get("target_tradeoff", fb_strat["target_tradeoff"]),
            "tool_assignments": raw.get("tool_assignments", fb_strat["tool_assignments"]),
            "parameters": raw.get("parameters", fb_strat["parameters"]),
            "predictions": predictions
        }

    # Deterministic 100% Feature Conservation Safety Gate:
    # If the primary roughing tool cannot physically clear the narrowest cavity,
    # secondary finishing / rest-machining with a smaller tool MUST be active across all strategies!
    min_cavity = features.get("machinability_constraints", {}).get("min_cavity_width_mm")
    if min_cavity is None:
        pockets = features.get("features", {}).get("setup_1_top_3axis", {}).get("pockets", [])
        widths = []
        for p in pockets:
            if p.get("is_slot") and p.get("slot_info"):
                widths.append(p["slot_info"].get("slot_width_mm", 10.0))
            else:
                b = p.get("bounds", {})
                widths.append(min(b.get("width_x_mm", 100.0), b.get("length_y_mm", 100.0)))
        min_cavity = min(widths) if widths else 50.0

    for key in ["CYCLE_TIME", "ACCURACY_TUNED", "BALANCED"]:
        strat = norm["strategies"][key]
        r_tool_num = strat["tool_assignments"].get("pocket_roughing", 1)
        r_tool = next((t for t in tools["tools"] if t["tool_number"] == r_tool_num), tools["tools"][0])

        if min_cavity <= r_tool["diameter_mm"]:
            f_tool_num = strat["tool_assignments"].get("pocket_finishing", 2)
            f_tool = next((t for t in tools["tools"] if t["tool_number"] == f_tool_num), None)
            if not f_tool or f_tool["diameter_mm"] >= min_cavity:
                smaller_tool = next((t for t in tools["tools"] if t["type"] == "flat_endmill" and t["diameter_mm"] < min_cavity), None)
                if smaller_tool:
                    strat["tool_assignments"]["pocket_finishing"] = smaller_tool["tool_number"]
            strat["parameters"]["pocket_finishing"]["enabled"] = True

    return norm

def call_gemini_api(api_key, features, tools, model="gemini-3.1-flash-lite", feedback=None):
    """
    Calls Google Gemini REST API directly via urllib.
    Incorporates diagnostic feedback if available for closed-loop self-correction.
    """
    api_key = (api_key or "").strip().strip('"').strip("'")
    model = (model or "gemini-3.1-flash-lite").strip().strip('"').strip("'")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    feedback_section = ""
    if feedback and feedback.get("actionable_feedback"):
        feedback_section = f"""
CRITICAL VERIFICATION FEEDBACK FROM PREVIOUS SIMULATION & AUDIT:
The previous CAM strategies were physically simulated in CAMotics and measured with OpenCASCADE metrology.
The following violations were detected and MUST be resolved in this iteration:
{json.dumps(feedback.get("actionable_feedback", []), indent=2)}

DIAGNOSTIC CRITIQUE PER STRATEGY:
{json.dumps(feedback.get("strategies", {}), indent=2)}

MANDATORY REVISION INSTRUCTIONS:
1. Address EVERY failure listed above.
2. If floor scallop or surface deviation exceeded tolerance, reduce finishing stepover, adjust stepdown, or select an appropriate finishing tool from the tool library.
3. If chipload exceeded limits, reduce roughing feedrate.
4. If rapid collision or gouging occurred, adjust clearance plane or stock offsets.
5. Retain what worked well and output the corrected, complete 3 strategies JSON.
"""

    prompt = f"""
{SYSTEM_PROMPT}

CAD FEATURES EXTRACTED FROM DRAWING:
{json.dumps(features, indent=2)}

AVAILABLE TOOL LIBRARY:
{json.dumps(tools, indent=2)}
{feedback_section}
Generate the three strategies matching this EXACT JSON schema structure:
{SCHEMA_TEMPLATE}

Return ONLY raw valid JSON conforming to the schema above.
"""

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        
    text_out = res_data["candidates"][0]["content"]["parts"][0]["text"]
    if text_out.startswith("```json"):
        text_out = text_out.split("```json", 1)[1].split("```", 1)[0].strip()
    elif text_out.startswith("```"):
        text_out = text_out.split("```", 1)[1].split("```", 1)[0].strip()
        
    raw_json = json.loads(text_out)
    return normalize_strategies(raw_json, features, tools, feedback=feedback)


def plan_strategies(features_path, tools_path, output_path, api_key=None, model=None, feedback_path=None):
    model = model or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    print("=" * 70)
    print(" [Step 2] GEMINI LLM MACHINING STRATEGY PLANNER")
    print(f" Input Features : {features_path}")
    print(f" Tool Library   : {tools_path}")
    print(f" Output Plan    : {output_path}")
    if feedback_path and os.path.exists(feedback_path):
        print(f" Feedback Path  : {feedback_path}")
    print("=" * 70)

    with open(features_path) as f:
        features = json.load(f)
    with open(tools_path) as f:
        tools = json.load(f)

    feedback = None
    if feedback_path and os.path.exists(feedback_path):
        with open(feedback_path, "r") as f:
            feedback = json.load(f)
        fb_directives = feedback.get("actionable_feedback", [])
        print(f"\n[🔄] Consuming {len(fb_directives)} diagnostic feedback directives for closed-loop revision:")
        for directive in fb_directives:
            print(f"   ↳ {directive}")

    gemini_key = api_key or os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        gemini_key = gemini_key.strip().strip('"').strip("'")
    if model:
        model = model.strip().strip('"').strip("'")
    strategies = None

    if gemini_key:
        print(f"\n[+] Active Gemini API Key detected! Querying {model}...")
        try:
            strategies = call_gemini_api(gemini_key, features, tools, model=model, feedback=feedback)
            print("[✓] Successfully received revised machining strategies from Gemini!")
        except urllib.error.HTTPError as e:
            err_msg = ""
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                err_msg = err_body.get("error", {}).get("message", str(e))
            except Exception:
                err_msg = str(e)
            print(f"[!] Warning: Gemini API call failed (HTTP {e.code}: {err_msg}). Falling back to deterministic planner.")
            strategies = build_deterministic_fallback(features, tools, feedback=feedback)
        except Exception as e:
            print(f"[!] Warning: Gemini API call failed ({e}). Falling back to deterministic planner.")
            strategies = build_deterministic_fallback(features, tools, feedback=feedback)
    else:
        print("\n[i] Note: No GEMINI_API_KEY set. Using deterministic CNC physics planner.")
        print("    (To use live Gemini, run: export GEMINI_API_KEY='your-key')")
        strategies = build_deterministic_fallback(features, tools, feedback=feedback)

    with open(output_path, "w") as f:
        json.dump(strategies, f, indent=2)

    # Print summary
    print("\n[+] Proposed Pareto Frontier Strategies Generated:")
    for key, strat in strategies["strategies"].items():
        params = strat.get("parameters", {})
        r_params = params.get("pocket_roughing", {})
        f_params = params.get("pocket_finishing", {})
        print(f"\n  [{key}] : {strat['name']}")
        print(f"    • Goal         : {strat['target_tradeoff']}")
        print(f"    • Roughing     : Tool #{strat['tool_assignments'].get('pocket_roughing')} | "
              f"Feed: {r_params.get('feedrate_mm_min')} mm/min, Stepover: {r_params.get('stepover_pct')}%, "
              f"Stepdown: {r_params.get('stepdown_mm')} mm")
        if f_params.get("enabled"):
            print(f"    • Finishing    : Tool #{strat['tool_assignments'].get('pocket_finishing')} | "
              f"Feed: {f_params.get('feedrate_mm_min')} mm/min, Stepover: {f_params.get('stepover_pct')}%, "
              f"Spring Passes: {f_params.get('spring_passes')}")
        else:
            print(f"    • Finishing    : Skipped (Single-pass aggressive)")

    print(f"\n-> Saved strategies to: {output_path}")
    print("=" * 70)
    return strategies


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM CAM Strategy Planner")
    parser.add_argument("--features", default=os.path.join(AGENT_DIR, "features.json"), help="Path to features.json")
    parser.add_argument("--tools", default=os.path.join(AGENT_DIR, "tool_library.json"), help="Path to tool_library.json")
    parser.add_argument("--feedback", default=None, help="Path to critique.json from previous iteration")
    parser.add_argument("--out", "--output", default=os.path.join(AGENT_DIR, "strategies.json"), help="Path to output strategies.json")
    parser.add_argument("--api-key", default=None, help="Gemini API Key")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"), help="Gemini Model")
    args = parser.parse_args()

    plan_strategies(args.features, args.tools, args.out, api_key=args.api_key, model=args.model, feedback_path=args.feedback)

