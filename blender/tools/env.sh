# env.sh — single place for the Blender/Godot binary paths every tools/*.sh script shells out to.
# Sourced (never executed directly) as `source "$BP/tools/env.sh"` after BP (blender)
# is computed. Update the binary here once instead of editing every build_*.sh script when Godot
# gets upgraded/reinstalled (2026-07-27, user-reported: the binary moved from the old
# version-suffixed path to a plain one after an update, and had to be fixed in 4 separate scripts).
#
# Both still honor an existing environment override (`GODOT=... tools/build_piece.sh ...`) — this
# file only supplies the DEFAULT when the caller hasn't already set one.
#
# 2026-09-08: this is the STOCK Godot binary now, not a custom build with the JVM module compiled
# in. godot-jvm 1.0.0-dev3 ships as the `addons/jvm/` GDExtension, so the runtime comes from the
# project. Point this at the old `godot.linuxbsd.editor.x86_64.jvm` and it loads the runtime TWICE:
# "Attempt to register extension class 'JvmScript', which appears to be already registered", then
# "Version mismatch! C++ module is : 0.17.1-4.7.2 / Jar is : 1.0.0-dev3", then every AutoLoad
# failing with "does not inherit from 'Node'" — which reads as a broken project, not a wrong binary.
BLENDER="${BLENDER:-blender}"
GODOT="${GODOT:-/data/danilko/bin/Godot_v4.7.2-stable_linux.x86_64}"
