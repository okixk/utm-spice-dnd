import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'com.utmapp.Dnd.Target1';
const OBJECT_PATH = '/com/utmapp/Dnd/Target';

const INTERFACE_XML = `
<node>
  <interface name="com.utmapp.Dnd.Target1">
    <method name="InspectDrop">
      <arg name="display" type="i" direction="in"/>
      <arg name="x" type="d" direction="in"/>
      <arg name="y" type="d" direction="in"/>
      <arg name="framebufferWidth" type="d" direction="in"/>
      <arg name="framebufferHeight" type="d" direction="in"/>
      <arg name="result" type="s" direction="out"/>
    </method>
  </interface>
</node>`;

function pointInRect(x, y, rect) {
    return x >= rect.x && y >= rect.y &&
        x < rect.x + rect.width && y < rect.y + rect.height;
}

function actorRect(actor) {
    if (!actor?.visible)
        return null;

    const [x, y] = actor.get_transformed_position();
    const [width, height] = actor.get_transformed_size();
    return {x, y, width, height};
}

export default class UtmDndTargetExtension extends Extension {
    enable() {
        this._debug = GLib.getenv('UTM_DND_DEBUG') === '1';
        this._dbusObject = Gio.DBusExportedObject.wrapJSObject(INTERFACE_XML, this);
        this._dbusObject.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            null,
            null);
    }

    disable() {
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        this._dbusObject?.unexport();
        this._dbusObject = null;
    }

    InspectDrop(display, x, y, framebufferWidth, framebufferHeight) {
        const result = this._inspectDrop(
            this._captureSnapshot(), display, x, y, framebufferWidth, framebufferHeight);
        if (this._debug)
            console.log(`UTM DnD target: ${JSON.stringify(result)}`);
        return JSON.stringify(result);
    }

    _captureSnapshot() {
        const workspace = global.workspace_manager.get_active_workspace();
        let windows = global.get_window_actors()
            .map(actor => actor.meta_window)
            .filter(window => window && !window.minimized &&
                window.showing_on_its_workspace() &&
                window.located_on_workspace(workspace));
        windows = global.display.sort_windows_by_stacking(windows).reverse();

        return {
            monotonicTime: GLib.get_monotonic_time(),
            monitors: Main.layoutManager.monitors.map(monitor => ({
                x: monitor.x,
                y: monitor.y,
                width: monitor.width,
                height: monitor.height,
            })),
            panel: actorRect(Main.panel),
            locked: Main.sessionMode.isLocked,
            overview: Main.overview.visible || Main.overview.visibleTarget,
            windows: windows.map(window => {
                const rect = window.get_frame_rect();
                const app = Shell.WindowTracker.get_default().get_window_app(window);
                return {
                    appId: app?.get_id() ?? window.get_gtk_application_id() ?? '',
                    gtkWindowPath: window.get_gtk_window_object_path() ?? '',
                    wmClass: window.get_wm_class() ?? '',
                    title: window.get_title() ?? '',
                    windowType: window.get_window_type(),
                    frame: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                };
            }),
        };
    }

    _inspectDrop(snapshot, display, x, y, framebufferWidth, framebufferHeight) {
        if (![x, y, framebufferWidth, framebufferHeight].every(Number.isFinite) ||
            framebufferWidth <= 0 || framebufferHeight <= 0) {
            return {kind: 'unsupported', confidence: 'low', reason: 'invalid-coordinates'};
        }

        const monitor = snapshot.monitors[display];
        if (!monitor) {
            return {kind: 'unsupported', confidence: 'low', reason: 'unknown-display'};
        }

        const stageX = monitor.x + x * monitor.width / framebufferWidth;
        const stageY = monitor.y + y * monitor.height / framebufferHeight;
        const diagnostic = {
            display,
            stageX,
            stageY,
            monitor: {x: monitor.x, y: monitor.y, width: monitor.width, height: monitor.height},
        };

        if (!pointInRect(stageX, stageY, monitor))
            return {...diagnostic, kind: 'unsupported', confidence: 'low', reason: 'outside-monitor'};

        if (snapshot.locked)
            return {...diagnostic, kind: 'unsupported', confidence: 'high', reason: 'locked'};

        if (snapshot.overview)
            return {...diagnostic, kind: 'unsupported', confidence: 'high', reason: 'overview'};

        const panel = snapshot.panel;
        if (panel && pointInRect(stageX, stageY, panel))
            return {...diagnostic, kind: 'unsupported', confidence: 'high', reason: 'shell-panel'};

        for (const window of snapshot.windows) {
            if (!pointInRect(stageX, stageY, window.frame))
                continue;

            const windowResult = {
                ...diagnostic,
                ...window,
            };

            const identity = `${window.appId} ${window.wmClass}`.toLowerCase();
            if (window.windowType === Meta.WindowType.DESKTOP ||
                identity.includes('rastersoft.ding') ||
                identity.includes('desktop-icons')) {
                return {...windowResult, kind: 'desktop', confidence: 'high'};
            }

            return {...windowResult, kind: 'window', confidence: 'high'};
        }

        return {...diagnostic, kind: 'desktop', confidence: 'high', reason: 'desktop-background'};
    }
}
