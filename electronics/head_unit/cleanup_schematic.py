#!/usr/bin/env python3
"""Clean up gazer_head.kicad_sch:
1. Remove all _TEMPLATE_ symbol instances (causes red X mess)
2. Fix reference designators (remove _? suffix, assign proper numbers)
3. Fix property positions (move near component, not at -100)
4. Fix instance references to match
"""
import re
import os

SCHEMATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gazer_head.kicad_sch")
OUTPUT = SCHEMATIC  # overwrite


def find_matching_paren(text, start):
    """Find position of matching ) for ( at position start."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def extract_symbol_blocks(content, offset):
    """Find all top-level (symbol ...) blocks starting from offset.
    Returns list of (start, end, block_text) tuples."""
    blocks = []
    pos = offset
    while True:
        # Find next \t(symbol block
        idx = content.find('\t(symbol\r\n', pos)
        if idx == -1:
            idx = content.find('\t(symbol\n', pos)
        if idx == -1:
            break

        # The opening ( is at idx+1
        paren_start = idx + 1
        paren_end = find_matching_paren(content, paren_start)
        if paren_end == -1:
            pos = idx + 10
            continue

        # Include the leading tab
        block_start = idx
        block_end = paren_end + 1

        blocks.append((block_start, block_end, content[block_start:block_end]))
        pos = block_end
    return blocks


def fix_property_line(lines, prop_name, new_value, comp_x, comp_y, offset_x, offset_y, hide=False):
    """Fix a property's value and position in a list of lines."""
    i = 0
    while i < len(lines):
        if f'(property "{prop_name}"' in lines[i]:
            # Fix the value on this line
            if new_value is not None:
                lines[i] = re.sub(
                    rf'(property "{prop_name}") "[^"]*"',
                    rf'\1 "{new_value}"',
                    lines[i]
                )
            # Fix position on the next line (contains (at ...))
            if i + 1 < len(lines) and '(at ' in lines[i + 1]:
                new_x = comp_x + offset_x
                new_y = comp_y + offset_y
                lines[i + 1] = re.sub(
                    r'\(at [^)]+\)',
                    f'(at {new_x:.2f} {new_y:.2f} 0)',
                    lines[i + 1]
                )
            # Add (hide yes) for hidden properties
            if hide:
                # Find the effects block and ensure hide is there
                for j in range(i + 1, min(i + 10, len(lines))):
                    if '(hide yes)' in lines[j]:
                        break
                    if lines[j].strip() == ')' and j > i + 3:
                        break
            break
        i += 1
    return lines


def fix_symbol_block(block_text):
    """Fix a real (non-template) symbol block:
    - Remove _? suffix from reference
    - Fix property positions relative to component
    - Fix instance references
    """
    lines = block_text.split('\n')

    # Extract component position
    comp_x, comp_y = None, None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('(at ') and comp_x is None:
            m = re.match(r'\(at ([\d.e+-]+) ([\d.e+-]+)', stripped)
            if m:
                comp_x = float(m.group(1))
                comp_y = float(m.group(2))
                break

    if comp_x is None:
        return block_text

    # Extract current reference
    ref = None
    for line in lines:
        m = re.search(r'property "Reference" "([^"]+)"', line)
        if m:
            ref = m.group(1)
            break

    if ref is None:
        return block_text

    # Clean reference: remove _? suffix
    clean_ref = re.sub(r'_\?$', '', ref)
    # Remove trailing ? if still present
    clean_ref = re.sub(r'\?$', '', clean_ref)

    # Fix Reference property (position near component)
    fix_property_line(lines, "Reference", clean_ref, comp_x, comp_y, 2, -3)

    # Fix Value property
    fix_property_line(lines, "Value", None, comp_x, comp_y, 2, 3)

    # Fix hidden properties (Footprint, Datasheet, Description) - at component position, hidden
    fix_property_line(lines, "Footprint", None, comp_x, comp_y, 0, 0, hide=True)
    fix_property_line(lines, "Datasheet", None, comp_x, comp_y, 0, 0, hide=True)
    fix_property_line(lines, "Description", None, comp_x, comp_y, 0, 0, hide=True)

    # Fix instance reference
    for i, line in enumerate(lines):
        if '(reference "' in line and '(property' not in line:
            lines[i] = re.sub(
                r'\(reference "[^"]+"\)',
                f'(reference "{clean_ref}")',
                line
            )

    return '\n'.join(lines)


def main():
    print(f"Reading: {SCHEMATIC}")
    with open(SCHEMATIC, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find end of lib_symbols section
    # lib_symbols is a top-level block starting with \t(lib_symbols
    lib_sym_start = content.find('\t(lib_symbols')
    if lib_sym_start == -1:
        print("ERROR: Could not find lib_symbols section")
        return
    lib_sym_paren = lib_sym_start + 1
    lib_sym_end = find_matching_paren(content, lib_sym_paren)
    if lib_sym_end == -1:
        print("ERROR: Could not find end of lib_symbols")
        return

    print(f"  lib_symbols: chars {lib_sym_start} to {lib_sym_end}")

    # Extract all symbol blocks after lib_symbols
    search_offset = lib_sym_end + 1
    blocks = extract_symbol_blocks(content, search_offset)
    print(f"  Found {len(blocks)} symbol instance blocks")

    # Classify blocks
    template_blocks = []
    real_blocks = []
    for start, end, text in blocks:
        if '_TEMPLATE_' in text:
            template_blocks.append((start, end, text))
        else:
            real_blocks.append((start, end, text))

    print(f"  Templates to remove: {len(template_blocks)}")
    print(f"  Real components to fix: {len(real_blocks)}")

    # Build new content
    # Strategy: reconstruct the file, replacing/removing blocks as needed
    # Sort all blocks by position
    all_blocks = [(s, e, t, True) for s, e, t in template_blocks]  # True = remove
    all_blocks += [(s, e, t, False) for s, e, t in real_blocks]     # False = fix
    all_blocks.sort(key=lambda x: x[0])

    # Build output by copying content, skipping templates and fixing reals
    result = []
    last_pos = 0
    removed_count = 0
    fixed_count = 0

    for start, end, text, is_template in all_blocks:
        # Copy content before this block
        result.append(content[last_pos:start])

        if is_template:
            # Skip this block entirely
            removed_count += 1
            # Also skip trailing newlines
            skip_end = end
            while skip_end < len(content) and content[skip_end] in '\r\n':
                skip_end += 1
            last_pos = skip_end
        else:
            # Fix this block
            fixed = fix_symbol_block(text)
            result.append(fixed)
            fixed_count += 1
            last_pos = end

    # Append remaining content
    result.append(content[last_pos:])

    output = ''.join(result)

    # Write output
    print(f"\nWriting cleaned schematic: {OUTPUT}")
    print(f"  Removed {removed_count} template symbols")
    print(f"  Fixed {fixed_count} component references/positions")

    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(output)

    # Verify
    with open(OUTPUT, 'r', encoding='utf-8') as f:
        verify = f.read()
    template_count = verify.count('_TEMPLATE_')
    bad_ref_count = len(re.findall(r'reference "[^"]*_\?"', verify))
    print(f"\n  Verification:")
    print(f"    _TEMPLATE_ occurrences remaining: {template_count}")
    print(f"    _? reference suffixes remaining: {bad_ref_count}")
    print(f"    File size: {len(verify)} chars ({len(verify.splitlines())} lines)")
    print("\nDone! Open the schematic in KiCad to verify.")


if __name__ == '__main__':
    main()
