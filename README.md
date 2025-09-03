This postprocessing script allows you to inject gcode at the start of a feature.
Supported features(Prusaslicer, Orcaslicer, Bambustudio):


```
external-perimeter
internal-perimeter
overhang-perimeter
infill
solid-infill
top-surface
bottom-surface
bridge
support-interface
support
```

Use with:
```
number               -> nozzle temperature
-fan + number        -> fan speed in %
-flow + number       -> flow multiplier
-gcode + string      -> Custom gcode at start of featureblock
-gcode-exit + string -> Custom gcode at start of featureblock
 ```
Additional parameters:
```
--wait-temp -> use M109 for temperature changes to wait for heatup
--skip-first-layers + number -> skip a select count of layers
```

Fan, Temperature and Flow will get reset automatically to the original value once a featureblock ends.

Sample usage:

```
"C:\PathToPython\python.exe" "C:\PathToScript\CustomFeatureSettings.py" --external-perimeter 250 --infill-flow 1.3 --wait-temp --skip-first-layers 3 --overhang-perimeter-fan 50 --support-gcode "M107" --bridge-gcode-exit "G1 Z10"
```
(Sets the temperature for external perimeters to 250, multiplies the infill flow with 1.3, waits for hotend to get to temperature, skips the first 3 layers, sets the fan to 50% on overhang perimeters, emits M107 at the start of a support section, emits G1 Z10 at the end of a bridge section) 
