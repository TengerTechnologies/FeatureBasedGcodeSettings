# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Copyright (c) 2025 Roman Tenger

import re
import sys
import logging
import argparse
from typing import Dict, Optional, List

FEATURE_SYNONYMS = {
    "external_perimeter": ["external perimeter", "external perimeters", "external wall", "outer wall", "wall-outer", "wall outer"],
    "internal_perimeter": ["perimeter", "perimeters", "inner wall", "wall-inner", "wall inner"],
    "overhang_perimeter": ["overhang perimeter", "overhang wall", "wall-overhang", "overhang-wall"],
    "infill": ["infill", "internal infill"],
    "solid_infill": ["solid infill", "solid-infill"],
    "top_surface": ["top solid infill", "skin top", "top surface", "top surfaces", "topskin"],
    "bottom_surface": ["bottom solid infill", "skin bottom", "bottom surface", "bottom surfaces", "bottomskin"],
    "bridge": ["bridge", "bridges", "bridge infill", "bridge-infill"],
    "support_interface": ["support material interface", "support interface"],
    "support": ["support material", "support", "supports"],
}

SYNONYM_TO_CANONICAL: Dict[str, str] = {}
def norm_key(s: str) -> str:
    s = s.strip().lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    return s

NORM_TO_CANONICAL: Dict[str, str] = {}
for canon, syns in FEATURE_SYNONYMS.items():
    for s in syns:
        s_clean = s.strip().lower()
        SYNONYM_TO_CANONICAL[s_clean] = canon
        NORM_TO_CANONICAL[norm_key(s)] = canon

TYPE_PREFIX_RE = re.compile(r"^\s*;\s*TYPE\s*:\s*(.+)$", re.IGNORECASE)

TEMP_CMD_RE = re.compile(r"^\s*M10(?:4|9)\b[^;]*\bS(-?\d+(?:\.\d+)?)", re.IGNORECASE)
FAN_ON_RE   = re.compile(r"^\s*M106\b[^;]*\bS(\d+)", re.IGNORECASE)
FAN_OFF_RE  = re.compile(r"^\s*M107\b", re.IGNORECASE)


MOVE_RE     = re.compile(r"^\s*G0?1\b", re.IGNORECASE)
E_WORD_RE   = re.compile(r"(?i)\bE([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")
Z_WORD_RE   = re.compile(r"\bZ([-+]?\d*\.?\d+)")

LAYER_NUM_RE      = re.compile(r"^\s*;LAYER:\s*(-?\d+)", re.IGNORECASE)
LAYER_WORD_RE     = re.compile(r"^\s*;\s*layer\s+(-?\d+)\b", re.IGNORECASE)
LAYER_CHANGE_RE   = re.compile(r"^\s*;(?:BEFORE_)?LAYER_CHANGE\b", re.IGNORECASE)


def match_canonical(type_value: str) -> Optional[str]:
    if not type_value:
        return None
    primary = re.split(r"[;|,]", type_value, maxsplit=1)[0].strip()
    key_exact = primary.lower()
    canon = SYNONYM_TO_CANONICAL.get(key_exact)
    if canon:
        return canon
    return NORM_TO_CANONICAL.get(norm_key(primary))

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))

def pct_to_pwm(pct: float) -> int:
    return clamp(int(round((pct / 100.0) * 255.0)), 0, 255)

def fmt_temp(t: float) -> str:
    return str(int(t)) if float(t).is_integer() else str(t)

def fmt_float(value: float, decimals: int) -> str:
    s = f"{value:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def process_gcode(
    input_file: str,
    wait_temp: bool = False,
    skip_first_layers: int = 0,
    flow_decimals: int = 5,
    feature_temps: Dict[str, float] = None,
    feature_fans_pct: Dict[str, float] = None,
    feature_flow: Dict[str, float] = None,
    feature_gcode_enter: Dict[str, List[str]] = None,
    feature_gcode_exit: Dict[str, List[str]] = None,
):

    feature_temps = feature_temps or {}
    feature_fans_pct = feature_fans_pct or {}
    feature_flow = feature_flow or {}
    feature_gcode_enter = feature_gcode_enter or {}
    feature_gcode_exit = feature_gcode_exit or {}

    try:
        with open(input_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        logging.error("Input file not found: %s", input_file)
        sys.exit(1)

    out_lines: List[str] = []

    current_temp: Optional[float] = None
    current_fan_pwm: Optional[int] = None
  
    e_relative = False
    last_e_abs = 0.0
    last_z: Optional[float] = None

    baseline_temp: Optional[float] = None
    baseline_fan_pwm: Optional[int] = None

    prev_overrode_temp = False
    prev_overrode_fan = False

    prev_feature_canon: Optional[str] = None
    current_feature_canon: Optional[str] = None

    current_layer = -1

    cmd_temp = "M109" if wait_temp else "M104"

    def insert_raw(line: str):
        """Append injected G-code and update temp/fan/e-mode state if relevant."""
        nonlocal current_temp, current_fan_pwm, e_relative, last_e_abs
        if not line.endswith("\n"):
            out_lines.append(line + "\n")
            s = line
        else:
            out_lines.append(line)
            s = line.rstrip("\n")


        if re.search(r"^\s*M82\b", s, re.IGNORECASE):
            e_relative = False
        elif re.search(r"^\s*M83\b", s, re.IGNORECASE):
            e_relative = True
        elif re.search(r"^\s*G92\b", s, re.IGNORECASE):
            m = re.search(r"(?i)\bE([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", s)
            if m:
                try:
                    last_e_abs = float(m.group(1))
                except ValueError:
                    pass


        m_temp = TEMP_CMD_RE.match(s)
        if m_temp:
            try:
                current_temp = float(m_temp.group(1))
            except ValueError:
                pass
        elif FAN_OFF_RE.match(s):
            current_fan_pwm = 0
        else:
            m_fan = FAN_ON_RE.match(s)
            if m_fan:
                try:
                    current_fan_pwm = int(m_fan.group(1))
                except ValueError:
                    pass

    def insert_temp(target: float, reason: str):
        insert_raw(f"{cmd_temp} S{fmt_temp(target)} ; set temp ({reason})")

    def insert_fan_pwm(pwm: int, reason: str):
        insert_raw(f"M106 S{clamp(pwm,0,255)} ; set fan ({reason})")

    def apply_flow_to_line(line: str, factor: float) -> str:
        """Rewrite E on motion lines according to factor and extrusion mode (positive extrusion only)."""
        nonlocal last_e_abs, e_relative
        if factor is None or abs(factor - 1.0) < 1e-12:
            return line

        code_part, sep, comment = line.partition(";")
        if not MOVE_RE.match(code_part):
            return line

        m_e = E_WORD_RE.search(code_part)
        if not m_e:
            return line

        try:
            e_val = float(m_e.group(1))
        except ValueError:
            return line


        if e_relative:
            delta = e_val
        else:
            delta = e_val - last_e_abs


        if delta <= 0:
            if not e_relative:
                last_e_abs = e_val
            return line

        new_delta = delta * factor
        new_e = new_delta if e_relative else (last_e_abs + new_delta)
        new_e_str = fmt_float(new_e, flow_decimals)

        start, end = m_e.span(1)
        code_new = code_part[:start] + new_e_str + code_part[end:]

        if not e_relative:
            last_e_abs = new_e

        return code_new + (sep + comment if sep else "")

    def update_layer_from_line(original_line: str, processed_line: str):
        """Update current_layer using markers or Z-rise. Reset active feature on layer change."""
        nonlocal current_layer, current_feature_canon, last_z
        s = original_line  
        m = LAYER_NUM_RE.match(s) or LAYER_WORD_RE.match(s)
        if m:
            try:
                current_layer = int(m.group(1))
            except ValueError:
                pass
            current_feature_canon = None
            return
        if LAYER_CHANGE_RE.match(s):
            current_layer = (current_layer + 1) if current_layer >= 0 else 0
            current_feature_canon = None
            return

        code_part = processed_line.partition(";")[0]
        if MOVE_RE.match(code_part):
            mz = Z_WORD_RE.search(code_part)
            if mz:
                try:
                    z_val = float(mz.group(1))
                except ValueError:
                    z_val = None
                if z_val is not None:
                    if last_z is None:
                        last_z = z_val
                        if current_layer < 0:
                            current_layer = 0  
                    else:
                        if z_val > last_z + 1e-6:
                            current_layer = 0 if current_layer < 0 else current_layer + 1
                            current_feature_canon = None
                        last_z = z_val

    
    for line in lines:
      
        processed_line = line

        pass

    out_lines.clear()
    current_temp = None
    current_fan_pwm = None
    e_relative = False
    last_e_abs = 0.0
    last_z = None
    baseline_temp = None
    baseline_fan_pwm = None
    prev_overrode_temp = False
    prev_overrode_fan = False
    prev_feature_canon = None
    current_feature_canon = None
    current_layer = 0

    for line in lines:
        update_layer_from_line(line, processed_line)
        skip_active = (skip_first_layers > 0 and current_layer >= 0 and current_layer < skip_first_layers)

        factor = feature_flow.get(current_feature_canon) if (current_feature_canon and not skip_active) else None
        processed_line = apply_flow_to_line(line, factor) if factor is not None else line

        out_lines.append(processed_line)

        
        m_type = TYPE_PREFIX_RE.match(processed_line)
        if m_type:
            
            if current_layer < 0:
                current_layer = 0

            type_value = m_type.group(1)
            canon = match_canonical(type_value)
            current_feature_canon = canon  

            if not skip_active and canon:
                
                new_temp = feature_temps.get(canon)
                new_fan_pwm = pct_to_pwm(feature_fans_pct[canon]) if (canon in feature_fans_pct) else None
                new_enter_gcodes = feature_gcode_enter.get(canon, []) 
               
                prev_exit_gcodes = feature_gcode_exit.get(prev_feature_canon, []) if prev_feature_canon else []

              
                for gc in prev_exit_gcodes:
                    insert_raw(f"{gc} ; exit {prev_feature_canon}")

               
                if prev_overrode_temp and new_temp is None and baseline_temp is not None:
                    insert_temp(baseline_temp, "restore baseline at feature boundary")
                    current_temp = baseline_temp
                if prev_overrode_fan and new_fan_pwm is None and baseline_fan_pwm is not None:
                    insert_fan_pwm(baseline_fan_pwm, "restore baseline at feature boundary")
                    current_fan_pwm = baseline_fan_pwm

                
                baseline_temp = current_temp
                baseline_fan_pwm = current_fan_pwm

              
                for gc in new_enter_gcodes:
                    insert_raw(f"{gc} ; enter {canon}")

                
                if new_temp is not None:
                    insert_temp(new_temp, f"{canon}")
                    current_temp = new_temp
                    prev_overrode_temp = True
                else:
                    prev_overrode_temp = False

                if new_fan_pwm is not None:
                    insert_fan_pwm(new_fan_pwm, f"{canon}")
                    current_fan_pwm = new_fan_pwm
                    prev_overrode_fan = True
                else:
                    prev_overrode_fan = False

               
                prev_feature_canon = canon
            else:
                
                prev_feature_canon = canon
           

            continue

       
        s = processed_line.rstrip("\n")
        if re.search(r"^\s*M82\b", s, re.IGNORECASE):
            e_relative = False
        elif re.search(r"^\s*M83\b", s, re.IGNORECASE):
            e_relative = True
        elif re.search(r"^\s*G92\b", s, re.IGNORECASE):
            m = re.search(r"(?i)\bE([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", s)
            if m:
                try:
                    last_e_abs = float(m.group(1))
                except ValueError:
                    pass

        m_temp = TEMP_CMD_RE.match(s)
        if m_temp:
            try:
                current_temp = float(m_temp.group(1))
            except ValueError:
                pass
        elif FAN_OFF_RE.match(s):
            current_fan_pwm = 0
        else:
            m_fan = FAN_ON_RE.match(s)
            if m_fan:
                try:
                    current_fan_pwm = int(m_fan.group(1))
                except ValueError:
                    pass

        update_layer_from_line(line, processed_line)


    with open(input_file, "w", encoding="utf-8") as outfile:
        outfile.writelines(out_lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Per-feature temp/fan/flow/custom G-code insertion with auto-restore and optional layer skipping (in-place)."
    )
    parser.add_argument("input_file", help="Path to the input G-code file (modified in-place)")
    
    parser.add_argument("--wait-temp", dest="wait_temp", action="store_true",
                        help="Use M109 (wait) instead of M104 for temperature changes.")
    parser.add_argument("--skip-first-layers", dest="skip_first_layers",
                        type=int, default=0, help="Number of initial layers to leave untouched.")


 
    def add_feature_args(group_name: str):
        g = parser.add_argument_group(group_name.replace("_", " ").title())
        dash = group_name.replace("_", "-")
        g.add_argument(f"--{dash}", type=float, help=f"Temperature (°C) for {group_name.replace('_',' ')}")
        g.add_argument(f"--{dash}-fan", type=float, help=f"Fan percent 0..100 for {group_name.replace('_',' ')}")
        g.add_argument(f"--{dash}-flow", type=float, help=f"Flow factor for {group_name.replace('_',' ')} (e.g., 1.05)")
        g.add_argument(f"--{dash}-gcode", action="append",
                       help=f"Custom G-code at start of {group_name.replace('_',' ')} (can be used multiple times)")
        g.add_argument(f"--{dash}-gcode-exit", action="append",
                       help=f"Custom G-code at end of {group_name.replace('_',' ')} (can be used multiple times)")

    for key in FEATURE_SYNONYMS.keys():
        add_feature_args(key)

    args = parser.parse_args()

    feature_temps: Dict[str, float] = {}
    feature_fans_pct: Dict[str, float] = {}
    feature_flow: Dict[str, float] = {}
    feature_gcode_enter: Dict[str, List[str]] = {}
    feature_gcode_exit: Dict[str, List[str]] = {}

    for canon in FEATURE_SYNONYMS.keys():
        t = getattr(args, canon)
        if t is not None:
            feature_temps[canon] = float(t)
        f = getattr(args, f"{canon}_fan")
        if f is not None:
            feature_fans_pct[canon] = float(f)
        fl = getattr(args, f"{canon}_flow")
        if fl is not None:
            feature_flow[canon] = float(fl)
        ge = getattr(args, f"{canon}_gcode")
        if ge:
            feature_gcode_enter[canon] = ge
        gx = getattr(args, f"{canon}_gcode_exit")
        if gx:
            feature_gcode_exit[canon] = gx

    process_gcode(
        input_file=args.input_file,
        wait_temp=args.wait_temp,
        skip_first_layers=args.skip_first_layers,
        flow_decimals=5,
        feature_temps=feature_temps,
        feature_fans_pct=feature_fans_pct,
        feature_flow=feature_flow,
        feature_gcode_enter=feature_gcode_enter,
        feature_gcode_exit=feature_gcode_exit,
    )
