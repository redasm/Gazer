#!/usr/bin/env python3
"""Fix net label overlap: extend stub wires by 12mm so labels don't overlap with pins."""
import re
import os
import uuid

SCHEMATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gazer_head.kicad_sch")

def main():
    with open(SCHEMATIC, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse all wires: extract (x1,y1)→(x2,y2) with their positions in the file
    wire_pattern = re.compile(
        r'\t\(wire\r?\n'
        r'\t\t\(pts\r?\n'
        r'\t\t\t\(xy ([\d.e+-]+) ([\d.e+-]+)\) \(xy ([\d.e+-]+) ([\d.e+-]+)\)\r?\n'
        r'\t\t\)\r?\n'
        r'\t\t\(stroke\r?\n'
        r'\t\t\t\(width 0\)\r?\n'
        r'\t\t\t\(type default\)\r?\n'
        r'\t\t\)\r?\n'
        r'\t\t\(uuid "[^"]+"\)\r?\n'
        r'\t\)\r?\n',
        re.MULTILINE
    )

    # Parse all labels: extract name, position
    label_pattern = re.compile(
        r'\t\(label "([^"]+)"\r?\n'
        r'\t\t\(at ([\d.e+-]+) ([\d.e+-]+) (\d+)\)\r?\n'
        r'\t\t\(effects\r?\n'
        r'\t\t\t\(font\r?\n'
        r'\t\t\t\t\(size [\d.]+ [\d.]+\)\r?\n'
        r'\t\t\t\)\r?\n'
        r'\t\t\t\(justify [^)]+\)\r?\n'
        r'\t\t\)\r?\n'
        r'\t\t\(uuid "([^"]+)"\)\r?\n'
        r'\t\)\r?\n',
        re.MULTILINE
    )

    # Collect wires
    wires = []
    for m in wire_pattern.finditer(content):
        x1, y1, x2, y2 = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
        wires.append({
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'start': m.start(), 'end': m.end(), 'text': m.group()
        })

    # Collect labels
    labels = []
    for m in label_pattern.finditer(content):
        name = m.group(1)
        lx, ly = float(m.group(2)), float(m.group(3))
        angle = int(m.group(4))
        uid = m.group(5)
        labels.append({
            'name': name, 'x': lx, 'y': ly, 'angle': angle, 'uuid': uid,
            'start': m.start(), 'end': m.end(), 'text': m.group()
        })

    print(f"Found {len(wires)} wires, {len(labels)} labels")

    # Match wire endpoints to labels (within 0.1mm tolerance)
    EXTEND_MM = 12.0  # extend by 12mm
    TOL = 0.2

    replacements = {}  # file_offset → new_text

    matched = 0
    for w in wires:
        # Wire endpoint is (x2, y2)
        for lb in labels:
            if abs(w['x2'] - lb['x']) < TOL and abs(w['y2'] - lb['y']) < TOL:
                # Match found! Extend wire in the same direction
                dx = w['x2'] - w['x1']
                dy = w['y2'] - w['y1']
                length = (dx**2 + dy**2) ** 0.5
                if length < 0.01:
                    continue

                # Normalize and extend
                nx, ny = dx / length, dy / length
                new_x2 = w['x1'] + nx * (length + EXTEND_MM)
                new_y2 = w['y1'] + ny * (length + EXTEND_MM)

                # Round to grid (0.01mm)
                new_x2 = round(new_x2, 2)
                new_y2 = round(new_y2, 2)

                # Generate new wire text
                new_wire = (
                    f'\t(wire\n'
                    f'\t\t(pts\n'
                    f'\t\t\t(xy {w["x1"]} {w["y1"]}) (xy {new_x2} {new_y2})\n'
                    f'\t\t)\n'
                    f'\t\t(stroke\n'
                    f'\t\t\t(width 0)\n'
                    f'\t\t\t(type default)\n'
                    f'\t\t)\n'
                    f'\t\t(uuid "{uuid.uuid4()}")\n'
                    f'\t)\n'
                )

                # Generate new label text (at extended position)
                new_label = (
                    f'\t(label "{lb["name"]}"\n'
                    f'\t\t(at {new_x2} {new_y2} {lb["angle"]})\n'
                    f'\t\t(effects\n'
                    f'\t\t\t(font\n'
                    f'\t\t\t\t(size 1.27 1.27)\n'
                    f'\t\t\t)\n'
                    f'\t\t\t(justify left bottom)\n'
                    f'\t\t)\n'
                    f'\t\t(uuid "{lb["uuid"]}")\n'
                    f'\t)\n'
                )

                replacements[w['start']] = (w['end'], new_wire)
                replacements[lb['start']] = (lb['end'], new_label)
                matched += 1
                break

    print(f"Matched {matched} wire-label pairs to extend")

    # Apply replacements (in reverse order to preserve positions)
    sorted_starts = sorted(replacements.keys(), reverse=True)
    for start in sorted_starts:
        end, new_text = replacements[start]
        content = content[:start] + new_text + content[end:]

    # Write output
    with open(SCHEMATIC, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Done! Extended {matched} stub wires by {EXTEND_MM}mm")
    print("Labels should no longer overlap with pin numbers.")


if __name__ == '__main__':
    main()
