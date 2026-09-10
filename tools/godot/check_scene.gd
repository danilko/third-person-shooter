extends SceneTree
## Loads and instantiates scenes named on the command line, reporting node/error counts.
##     Godot_v4.7.2-stable_linux.x86_64 --headless --script tools/godot/check_scene.gd -- <path>...
func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var bad := 0
	for path in args:
		var ps: PackedScene = load(path)
		if ps == null:
			print("FAIL  load    %s" % path); bad += 1; continue
		var n: Node = ps.instantiate()
		if n == null:
			print("FAIL  instance %s" % path); bad += 1; continue
		var count := _count(n)
		print("OK    %-52s %4d nodes, root %s (%s)" % [path, count, n.name, n.get_class()])
		n.free()
	quit(1 if bad else 0)

func _count(n: Node) -> int:
	var c := 1
	for ch in n.get_children():
		c += _count(ch)
	return c
