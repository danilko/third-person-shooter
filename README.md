# Godot Kotlin/JVM Third Person Experiment

A technical exploration of 3D game mechanics in **Godot 4.x** using the **Kotlin/JVM** binding. This project adapts and refactors traditional GDScript-based third-person controllers into a Java/Kotlin-compatible architecture.

## 🎮 Play the Game
A prebuilt binary is available on **itch.io**: [third-person-shooter-godot-jvm](https://danil-ko.itch.io/third-person-shooter-godot-jvm)

## 📺 Gameplay Video
Watch the gameplay demo on **YouTube**: [third-person-shooter-godot-jvm](https://youtu.be/CiJGKLYyk9Q)

## 🛠 Tech Stack
* **Engine:** Godot 4.6 (Custom [Utopia-Rise](https://github.com/utopia-rise/godot-kotlin-jvm) build required)
* **Language:** Java / Kotlin
* **JDK:** Amazon Corretto 25

## ✨ Features & Modifications
This project is based on Johnny Rouddro's Third Person Controller tutorial ([YouTube](https://www.youtube.com/watch?v=3AD2z2mx3sY)) but introduces several architectural changes and gameplay tweaks:

* **Decoupled Architecture:** Refactored the monolithic player class into a modular **Controller** system.
* **Movement Mechanics:** * Added **Double Jump** capability.
    * **Crawl-to-Shoot** mechanics (Experimental/Beta animation).
    * Dynamic **Physics Body transformation** during dodge rolls.
* **Combat Updates:**
    * Simplified weapon system (Always equipped, no holster state).
    * Customized weapon models and swap logic.
    * Toggleable over-the-shoulder camera (Left/Right swap).

* **Sample State Based Enemy:**
  * Use Navigation 3D Agent for movement
  * Use basic state machine for state
  * Basic damage systems for player and enemy
  * Simple choose of weapon and re-fill

---

## 🎯 Combat System

### Skeleton Hitbox & Damage Zones

Damage is resolved against the character's **physical ragdoll skeleton** (`PhysicalBoneSimulator3D`). Each `PhysicalBone3D` acts as an independent collider, so the hit location on the body determines the damage multiplier applied to the base weapon damage.

| Body Zone | Bones | Damage Multiplier |
|:----------|:------|:-----------------:|
| **Head / Neck** | `neck_01` | **4.0×** |
| **Upper Torso** | `spine_03`, `clavicle_l`, `clavicle_r` | 1.0× |
| **Mid / Lower Torso** | `spine_02`, `spine_01`, `pelvis` | 0.75× |
| **Arms** | `upperarm_l/r`, `lowerarm_l/r`, `hand_l/r` | 0.75× |
| **Legs** | `thigh_l/r`, `calf_l/r`, `foot_l/r` | 0.5× |

The hitbox colliders are the same bones that drive the ragdoll on death — no separate invisible hit-mesh is needed.

### Headshot Detection

A hit is classified as a **headshot** when the colliding bone is `Physical Bone neck_01`. This single bone covers both the neck capsule and the head sphere in the ragdoll setup.

```
hit bone == "Physical Bone neck_01"  →  headshot = true  →  4× damage
```

Headshot detection lives in `Health.takeDamage()` and is bone-name driven, so it works identically for both player and enemy characters.

### Kill Notifications (EventBus)

When any character's health reaches zero, `Health` emits a unified payload to the **EventBus** singleton:

```
EventBus.characterEliminated(attackerName, victimName, weaponName, headshot)
```

The player HUD (`CharacterHUD`) subscribes to this signal in `_ready()` and displays a 3-second notification:

| Situation | Example message |
|:----------|:----------------|
| Body shot kill | `Enemy Eliminated [Pistol] - Eliminated` |
| Headshot kill | `Enemy Eliminated [Pistol] - Headshot` |
| Player killed | `Player Eliminated [Rifile] - Eliminated` |

Character display names are configured via the `displayName` export property on each `Health` node. If left blank, the owning node's scene name is used as a fallback.

> **Note:** This is an experimental codebase. You may encounter "crunch-time" bugs or unstable animations. It is provided as-is for educational purposes.

---

## 🚀 Getting Started

### Prerequisites
You **cannot** use the standard Godot editor. You must download the specific Kotlin-JVM enabled editor from [Utopia-Rise Releases](https://github.com/utopia-rise/godot-kotlin-jvm).

### Build Instructions
1. Clone the repository.
2. Run the Gradle build task to generate the necessary JVM wrappers:
   ```bash
   ./gradlew build
   ```
3. Open the `project.godot` file using the **Godot Kotlin/JVM Editor**.

---

## 🎮 Controls

| Action                   | Input                        |
|:-------------------------|:-----------------------------|
| **Move**                 | `W` `A` `S` `D`              |
| **Jump / Double Jump**   | `Space`                      |
| **Roll**                 | `Ctrl` + Direction           |
| **Crouch / Crawl**       | `C` / `V`                    |
| **Aim / Fire**           | `Mouse Right` / `Mouse Left` |
| **Reload**               | `R`                          |
| **Switch Weapon**        | `G`                          |
| **Swap Camera Shoulder** | `Q`                          |
| **Menu**                 | `Esc`                        |

---

## 📚 Credits & Assets

### Code & Logic
* Base Third Person Controller by **Johnny Rouddro**: [YouTube](https://www.youtube.com/watch?v=3AD2z2mx3sY) | [GitHub](https://github.com/JohnnyRouddro/Godot_Third_Person_Controller) | [Itch.io](https://johnnyrouddro.itch.io/godot-4-third-person-controller)

### Models & External Assets
* **Weapon Models:** [50 Low-poly Guns](https://quaternius.itch.io/50-lowpoly-guns) by Quaternius.
* **Additional Assets:** [Godot Asset Library](https://godotengine.org/asset-library/asset/781).


Note:
Did use Gemini/Claude AI during debugging/documentation.

---

## 🎮 Screenshots
![screenshot1.png](images/screenshot1.png)
![screenshot2.png](images/screenshot2.png)
![screenshot3.png](images/screenshot3.png)
