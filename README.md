# SpacePilot in Fusion

The connected device is a **3Dconnexion SpacePilot** (SP1, USB `046d:c625`). macOS sees it, but the current 3Dconnexion driver for macOS no longer supports it. Fusion only talks to that driver, so the cap does nothing on its own.

This project reads the SpacePilot directly and moves the view in Fusion.

## Setup

From the project folder:

```bash
./build.sh
```

That builds the reader and installs the add-in here:

`~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/SpacePilot`

In Fusion:

1. Open a design.
2. **Utilities → ADD-INS → Scripts and Add-Ins**.
3. **Add-Ins** tab, select **SpacePilot**, click **Run**.
4. Check **Run on Startup**.

If macOS asks for **Input Monitoring**, allow Autodesk Fusion. Without that permission the add-in cannot read the cap.

**Utilities → ADD-INS** then has a **SpacePilot** command. It opens a panel with the six raw axes and the number of each button you press.

## Use

The cap moves the model the way a SpaceMouse does in Fusion: slide sideways and forward to pan, press in to zoom closer, tilt forward to orbit, and twist around the vertical axis to turn the model. The camera stays upright.

Speed and axis direction live in

`~/Library/Application Support/SpacePilotFusion/config.json`

The file is created on first launch and reloaded when you save it. The same values are in the SpacePilot panel. Under **Direction**, each row shows where the model goes. Green is the calibrated direction, yellow is reversed. Click a row to swap it. **Buttons** assigns an action to a key on the device: press the key, then pick the action.

`map` ties a motion to a raw axis. A leading minus reverses it.

| If this feels wrong | Entry |
| --- | --- |
| pushing right moves the model left | `"panX": "-tx"` |
| pushing forward moves the model down | `"panY": "ty"` |
| pressing in zooms out | `"zoom": "tz"` |
| tilt or twist is backwards | flip the sign on `pitch`, `yaw`, or `roll` |

Other values: `speed`, `panSpeed`, `zoomSpeed`, `orbitSpeed`, `rollSpeed`, `deadzone`. `objectMode` set to `false` flies the camera instead of moving the model. `dominant` keeps only the strongest axis.

## Buttons

Press a button on the SpacePilot. Its number appears in the panel, and you pick the action next to it: Fit, a standard view, Undo, Faster, or a held key such as Shift. Shift, Ctrl, Alt, Command, and Esc stay down while the button is held. Fusion needs **Accessibility** permission for that.

The same mapping is stored in `config.json` under `buttons`. Indexes start at 0.

## Check without Fusion

```bash
./bin/sp1hid --monitor
```

One line shows the raw values. Near 0 at rest, clearly above that when you move the cap. Stop with Ctrl+C.

The SpacePilot LCD shows the Fusion speed and the button assignments on the left. CPU, memory, and GPU use sit on the right and refresh about once a second. The device firmware paints its own logo back unless the add-in keeps refreshing the screen, so the logo returns when the add-in stops.
