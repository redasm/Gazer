#!/bin/bash
# Gazer Headless Setup for macOS (Apple Silicon Supported)

echo ">>> Gazer Headless Setup for macOS"

# 1. Check Homebrew
if ! command -v brew &> /dev/null; then
    echo "Error: Homebrew not found. Please install Homebrew first."
    exit 1
fi

# 2. Install BetterDisplay (Virtual Display Driver)
echo ">>> Installing BetterDisplay..."
brew install --cask betterdisplay

# 3. Instruction
echo "--------------------------------------------------------"
echo "✅ BetterDisplay Installed."
echo "ACTION REQUIRED:"
echo "1. Open 'BetterDisplay' from Applications."
echo "2. Grant permissions (Accessibility/Screen Recording) if asked."
echo "3. Click the BetterDisplay icon in Menu Bar -> 'Create New Virtual Display'."
echo "4. Create a '1920x1080' display."
echo "5. (Optional) Set it to 'Connect on startup'."
echo "--------------------------------------------------------"
echo "Now Gazer can 'see' your desktop even without a physical monitor!"
echo "--------------------------------------------------------"
