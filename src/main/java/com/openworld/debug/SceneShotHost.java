package com.openworld.debug;

import godot.annotation.Export;
import godot.annotation.Register;
import godot.annotation.Script;
import godot.api.Camera3D;
import godot.api.DirectionalLight3D;
import godot.api.Environment;
import godot.api.Image;
import godot.api.Node;
import godot.api.Node3D;
import godot.api.PackedScene;
import godot.api.ProceduralSkyMaterial;
import godot.api.ResourceLoader;
import godot.api.Sky;
import godot.api.WorldEnvironment;
import godot.core.Error;
import godot.core.Vector3;
import godot.global.GD;

/**
 * One-shot IN-ENGINE screenshot of a baked scene (the {@code WorldBaker}/{@code NavBaker} host
 * idiom): load a {@code .tscn}, point a camera at it, let it settle, save a PNG, quit.
 *
 * <pre>
 *   godot --path &lt;repo&gt; res://src/main/resources/com/openworld/world/hosts/SceneShot.tscn
 * </pre>
 *
 * <p><b>Why this exists.</b> Every picture of this world so far has been a Blender render, and
 * Blender is not the renderer the world ships on. That mattered immediately: a 4 km preview showed
 * even bands of sea straight through the ground, which looked exactly like missing terrain and was
 * neither — it was Blender's depth buffer at a 10⁶ near/far ratio. The question "does this world
 * z-fight" can only be answered by the engine that draws it, and answering it needed a picture from
 * inside Godot. Anything about draw order, depth precision, material or LOD is the same story.
 *
 * <p>NOT headless: {@code --headless} has no renderer at all, so the captured image is empty. Run it
 * on a real display (or under {@code xvfb-run}).
 *
 * <p>The camera's {@code near} is exported because it is the whole subject of the question above —
 * depth precision is the RATIO {@code far/near}, not {@code far} — so a shot can be taken at the
 * game's own values or at any other pair, and the two compared.
 */
@Script(className = "SceneShotHost")
public class SceneShotHost extends Node3D {

    @Export public String scenePath =
            "res://src/main/resources/com/openworld/world/pieces/Island_base.tscn";
    @Export public String outputPath = "user://scene_shot.png";
    /** Frames to let the scene stream, light and settle before the capture. */
    @Export public int delayFrames = 30;

    @Export public float camX = 0f;
    @Export public float camY = 260f;
    @Export public float camZ = 1600f;
    @Export public float aimX = 0f;
    @Export public float aimY = 0f;
    @Export public float aimZ = 0f;
    /** The game's own camera values by default — {@code Character.tscn}'s ActiveCamera. */
    @Export public float cameraNear = 0.1f;
    @Export public float cameraFar = 100000f;
    @Export public float cameraFov = 65f;
    @Export public boolean quitWhenDone = true;

    private int frames = 0;
    private boolean shot = false;

    @Register
    @Override
    public void _ready() {
        Object loaded = ResourceLoader.INSTANCE.load(scenePath, "", ResourceLoader.CacheMode.REUSE);
        if (!(loaded instanceof PackedScene packed)) {
            GD.printErr("SceneShotHost: cannot load " + scenePath);
            quit();
            return;
        }
        Node world = packed.instantiate();
        if (world == null) { GD.printErr("SceneShotHost: instantiate failed"); quit(); return; }
        addChild(world);

        // A baked piece carries no light or sky of its own — it is geometry. Give it the same
        // sky-lit setup the walk-test host uses, or every shot is a black rectangle.
        ProceduralSkyMaterial skyMat = new ProceduralSkyMaterial();
        Sky sky = new Sky();
        sky.setMaterial(skyMat);
        Environment env = new Environment();
        env.setBackground(Environment.BGMode.SKY);
        env.setSky(sky);
        env.setAmbientSource(Environment.AmbientSource.SKY);
        WorldEnvironment we = new WorldEnvironment();
        we.setEnvironment(env);
        addChild(we);

        DirectionalLight3D sun = new DirectionalLight3D();
        addChild(sun);
        sun.setPosition(new Vector3(0f, 400f, 0f));
        sun.lookAt(new Vector3(120f, 0f, 90f), Vector3.Companion.getUP());
        sun.setShadow(true);

        Camera3D cam = new Camera3D();
        addChild(cam);
        cam.setNear(cameraNear);
        cam.setFar(cameraFar);
        cam.setFov(cameraFov);
        cam.setPosition(new Vector3(camX, camY, camZ));
        cam.lookAt(new Vector3(aimX, aimY, aimZ), Vector3.Companion.getUP());
        cam.setCurrent(true);
        GD.print("SceneShotHost: " + scenePath + "  cam=(" + camX + "," + camY + "," + camZ
                + ") near=" + cameraNear + " far=" + cameraFar);
    }

    @Register
    @Override
    public void _process(double delta) {
        if (shot) return;
        if (++frames < delayFrames) return;
        shot = true;
        // The viewport texture is only valid AFTER a frame has been drawn, which is why this runs
        // from _process on a later frame rather than from _ready.
        Image img = getViewport() == null || getViewport().getTexture() == null
                ? null : getViewport().getTexture().getImage();
        if (img == null) {
            GD.printErr("SceneShotHost: no viewport image (running --headless?)");
        } else {
            Error err = img.savePng(outputPath);
            GD.print("SceneShotHost: " + (err == Error.OK ? "wrote " : "FAILED " + err + " ")
                    + outputPath + "  " + img.getWidth() + "x" + img.getHeight());
        }
        quit();
    }

    private void quit() {
        if (quitWhenDone && getTree() != null) getTree().quit();
    }
}
