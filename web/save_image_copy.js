// SaveImageCopy - frontend extension (v2)
//  - [💾 | 📁] half-and-half button row + backup_prefix field below the preview
//  - save_prefix moved to the very bottom of the node (+ bottom spacer)
//  - left click on the preview -> fullscreen (aspect ratio preserved)
//  - hand cursor on preview and on the buttons
//  - node drag / resize never triggers a click action
//
// Debug: run  window.__SIC_DEBUG = true  in the browser console to log click decisions.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_NAME = "SaveImageCopy";
const ROUTE = "/saveimagecopy";
const EDGE = 8;            // px from node border treated as "resize zone"
const TEXT_RESERVE = 22;   // px at the bottom of the preview area (resolution text)
const CLICK_TOL = 4;       // px; more movement than this = drag, not click
const GEOM_TOL = 2;        // px; node pos/size change allowed during a click
const CLOSE_DELAY = 300;   // ms; fullscreen ignores closing input right after opening
const MARGIN = 15;         // same side margin as default widgets
const GAP = 4;             // gap between the two buttons
const MIN_ROW_WIDTH = 80;  // minimum node width required by the button row
const BOTTOM_SPACE = 12;   // extra px under the last widget (+ ~4px widget gap ~= MARGIN)

// Width of ONE of the two half buttons, from the node's current width.
const rowButtonWidth = (node) => Math.max(0, (node.size[0] - MARGIN * 2 - GAP) / 2);

const dbg = (...a) => { if (globalThis.__SIC_DEBUG) console.log("[SaveImageCopy]", ...a); };
const LG = () => globalThis.LiteGraph;
const WH = () => LG()?.NODE_WIDGET_HEIGHT ?? 20;

// ---------------------------------------------------------------- helpers
function toast(ok, msg) {
    if (app.extensionManager?.toast?.add) {
        app.extensionManager.toast.add({
            severity: ok ? "success" : "warn",
            summary: "Save Image (Copy)",
            detail: msg,
            life: 4000,
        });
    } else if (!ok) {
        alert("Save Image (Copy): " + msg);
    }
}

async function post(route, body) {
    const resp = await api.fetchApi(route, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    return await resp.json();
}

function noSerialize(w) {
    w.serialize = false;
    w.options = Object.assign({}, w.options, { serialize: false });
}

function nodeKey(node) {
    return node.__sic?.key ?? String(node.id);
}

// Widgets that must stay at the bottom, in this order:
// [button row, backup_prefix field, save_prefix, bottom spacer]
function tailWidgets(node) {
    const s = node.__sic;
    if (!s) return [];
    const list = [s.row, s.pathW];
    const prefixW = node.widgets?.find((w) => w.name === "save_prefix");
    if (prefixW) list.push(prefixW);
    list.push(s.spacer);
    return list;
}

// Invisible widget that only adds empty space below the last input field.
function makeSpacer() {
    return {
        type: "sic_spacer",
        name: "sic_spacer",
        value: null,
        serialize: false,
        options: { serialize: false },
        computeSize() { return [MIN_ROW_WIDTH, BOTTOM_SPACE]; },
        draw() {},
    };
}

function ensureOrder(node) {
    const ws = node.widgets;
    if (!node.__sic || !ws) return;
    const tail = tailWidgets(node);
    const n = tail.length;
    const cur = ws.slice(-n);
    if (cur.length === n && cur.every((w, i) => w === tail[i])) return;
    for (const w of tail) {
        const i = ws.indexOf(w);
        if (i >= 0) ws.splice(i, 1);
    }
    ws.push(...tail);
    app.canvas?.setDirty(true, true);
}

// ------------------------------------------------------- button row widget
function makeButtonRow(node) {
    const row = {
        type: "sic_buttons",
        name: "sic_buttons",
        value: null,
        serialize: false,
        options: { serialize: false },
        pressed: null,        // "save" | "folder" | null
        last_y: null,
        // Fixed minimum width. Never echo the passed-in width back: that would
        // lock the node to whatever width it was last given.
        computeSize() {
            return [MIN_ROW_WIDTH, WH()];
        },
        draw(ctx, n, width, y, h) {
            const H = h ?? WH();
            this.last_y = y;
            const lg = LG();
            // Always use the node's CURRENT width (the `width` argument can be stale
            // after a resize), so the two halves always fill the row.
            const cw = rowButtonWidth(n);
            const items = [["save", "💾"], ["folder", "📁"]];
            ctx.save();
            items.forEach(([kind, label], i) => {
                const x = MARGIN + i * (cw + GAP);
                ctx.fillStyle = this.pressed === kind
                    ? (lg?.WIDGET_OUTLINE_COLOR ?? "#666")
                    : (lg?.WIDGET_BGCOLOR ?? "#222");
                ctx.strokeStyle = lg?.WIDGET_OUTLINE_COLOR ?? "#666";
                ctx.beginPath();
                if (ctx.roundRect) ctx.roundRect(x, y, cw, H, [H * 0.5]);
                else ctx.rect(x, y, cw, H);
                ctx.fill();
                ctx.stroke();
                ctx.fillStyle = lg?.WIDGET_TEXT_COLOR ?? "#ddd";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.font = "14px Arial";
                ctx.fillText(label, x + cw / 2, y + H * 0.5 + 1);
            });
            ctx.restore();
        },
    };
    return row;
}

// ------------------------------------------------------- preview geometry
function widgetY(w) {
    const y = w.last_y ?? w.y;
    return Number.isFinite(y) ? y : null;
}

function widgetBottom(node, w) {
    const y = widgetY(w) ?? 0;
    let h = w.computedHeight;
    if (h == null) {
        h = w.computeSize ? w.computeSize(node.size[0])[1] : WH();
    }
    return y + h;
}

// Preview area in node-local coordinates (y = 0 is the top of the node body)
function getPreviewRect(node) {
    const ws = node.widgets ?? [];
    const tail = tailWidgets(node);
    const previewIdx = ws.findIndex((w) => w.name === "$$canvas-image-preview");

    let top, bottom;
    if (previewIdx >= 0) {
        let t = 0;
        ws.forEach((w, i) => {
            if (i < previewIdx && !w.hidden) t = Math.max(t, widgetBottom(node, w));
        });
        top = t + 4;
        const after = tail.filter((w) => ws.indexOf(w) > previewIdx);
        const ys = after.map(widgetY).filter((y) => y != null);
        bottom = ys.length
            ? Math.min(...ys) - 2
            : node.size[1] - tail.length * (WH() + 4) - 8;
    } else {
        let t = 0;
        for (const w of ws) if (!w.hidden) t = Math.max(t, widgetBottom(node, w));
        top = t + 4;
        bottom = node.size[1] - 4;
    }
    const h = bottom - top;
    if (!(h > 0)) return null;
    return { x: 0, y: top, w: node.size[0], h };
}

function getImgEl(node) {
    const imgs = node.imgs;
    if (!imgs?.length) return null;
    return imgs[node.imageIndex ?? 0] ?? imgs[0];
}

function hasImage(node) {
    return !!(node.imgs?.length || node.images?.length);
}

function imageSrc(node) {
    const el = getImgEl(node);
    if (el?.src) return el.src;
    const im = node.images?.[node.imageIndex ?? 0] ?? node.images?.[0];
    if (!im) return null;
    const q = new URLSearchParams({
        filename: im.filename,
        subfolder: im.subfolder ?? "",
        type: im.type ?? "output",
    });
    return api.apiURL("/view?" + q.toString());
}

// What is under the pointer?  "save" | "folder" | "preview" | null
function hitAt(node, gx, gy) {
    if (!node?.__sic || node.flags?.collapsed) return null;
    const lx = gx - node.pos[0];
    const ly = gy - node.pos[1];
    if (lx < EDGE || lx > node.size[0] - EDGE) return null;
    if (ly < 0 || ly > node.size[1] - EDGE) return null;   // title bar / bottom edge

    // --- button row
    const row = node.__sic.row;
    const ry = row.last_y;
    if (Number.isFinite(ry) && ly >= ry && ly <= ry + WH()) {
        const cw = rowButtonWidth(node);
        if (lx >= MARGIN && lx <= MARGIN + cw) return "save";
        if (lx >= MARGIN + cw + GAP && lx <= node.size[0] - MARGIN) return "folder";
        return null;
    }

    // --- preview
    if (!hasImage(node)) return null;
    const r = getPreviewRect(node);
    if (!r) return null;
    if (lx < r.x || lx > r.x + r.w) return null;
    if (ly < r.y || ly > r.y + r.h - TEXT_RESERVE) return null;

    const el = getImgEl(node);
    const single = (node.imgs?.length ?? 1) === 1;
    const iw = el?.naturalWidth || 0;
    const ih = el?.naturalHeight || 0;
    if (single && iw && ih) {
        const s = Math.min(r.w / iw, r.h / ih);
        const dw = iw * s;
        const dh = ih * s;
        const x0 = r.x + (r.w - dw) / 2;
        const y0 = r.y + (r.h - dh) / 2;
        if (lx < x0 || lx > x0 + dw || ly < y0 || ly > y0 + dh) return null;
    }
    return "preview";
}

// -------------------------------------------------------------- fullscreen
let overlay = null;

function closeFullscreen() {
    if (!overlay) return;
    overlay.remove();
    overlay = null;
    window.removeEventListener("keydown", onKey, true);
}

function onKey(e) {
    if (e.key === "Escape") {
        e.stopPropagation();
        closeFullscreen();
    }
}

function openFullscreen(src) {
    if (!src || overlay) return;
    const el = document.createElement("div");
    overlay = el;
    Object.assign(el.style, {
        position: "fixed",
        inset: "0",
        zIndex: "2147483000",
        background: "rgba(0,0,0,0.92)",
        cursor: "pointer",
    });
    const img = document.createElement("img");
    img.src = src;
    img.draggable = false;
    Object.assign(img.style, {
        width: "100vw",
        height: "100vh",
        objectFit: "contain",   // keeps aspect ratio
        userSelect: "none",
        display: "block",
    });
    el.appendChild(img);

    const t0 = performance.now();
    let armed = false;
    // Closing needs a NEW press that starts on the overlay after the delay;
    // the click/pointerup left over from the opening click can never close it.
    el.addEventListener("pointerdown", (e) => {
        armed = e.button === 0 && performance.now() - t0 > CLOSE_DELAY;
        dbg("overlay pointerdown, armed =", armed);
    });
    el.addEventListener("click", () => {
        if (!armed) return;
        closeFullscreen();
    });
    document.body.appendChild(el);
    window.addEventListener("keydown", onKey, true);
    dbg("fullscreen opened");
}

// ------------------------------------------------- global pointer handling
function installPointerHandlers() {
    const canvasEl = app.canvas?.canvas;
    if (!canvasEl) return;

    const findNode = (e) => {
        const c = app.canvas;
        const [gx, gy] = c.convertEventToCanvasOffset(e);
        const node = c.graph?.getNodeOnPos(gx, gy);
        return { node, gx, gy };
    };

    // ---- hover cursor
    let cursorOwned = false;
    const releaseCursor = () => {
        if (cursorOwned) {
            canvasEl.style.cursor = "";
            cursorOwned = false;
        }
    };
    const onMove = (e) => {
        if (e.buttons !== 0 || overlay || e.target !== canvasEl) {
            releaseCursor();
            return;
        }
        const { node, gx, gy } = findNode(e);
        if (node && hitAt(node, gx, gy)) {
            canvasEl.style.cursor = "pointer";
            cursorOwned = true;
        } else {
            releaseCursor();
        }
    };
    canvasEl.addEventListener("pointermove", onMove);
    canvasEl.addEventListener("mousemove", onMove);
    canvasEl.addEventListener("pointerleave", releaseCursor);

    // ---- click detection (capture phase on window: not affected by litegraph)
    let pending = null;
    const clearPressed = () => {
        if (pending?.node?.__sic) {
            pending.node.__sic.row.pressed = null;
            app.canvas?.setDirty(true, true);
        }
    };

    window.addEventListener(
        "pointerdown",
        (e) => {
            pending = null;
            if (e.button !== 0 || overlay || e.target !== canvasEl) return;
            if (e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
            const { node, gx, gy } = findNode(e);
            const kind = node ? hitAt(node, gx, gy) : null;
            dbg("pointerdown hit =", kind);
            if (!kind) return;
            pending = {
                kind,
                node,
                x: e.clientX,
                y: e.clientY,
                pos: [node.pos[0], node.pos[1]],
                size: [node.size[0], node.size[1]],
            };
            if (kind !== "preview") {
                node.__sic.row.pressed = kind;
                app.canvas?.setDirty(true, true);
            }
        },
        true
    );

    window.addEventListener(
        "pointerup",
        (e) => {
            const p = pending;
            if (!p || e.button !== 0) { pending = null; return; }
            clearPressed();
            pending = null;

            const moved = Math.hypot(e.clientX - p.x, e.clientY - p.y);
            const n = p.node;
            const geomChanged =
                Math.abs(n.pos[0] - p.pos[0]) > GEOM_TOL || Math.abs(n.pos[1] - p.pos[1]) > GEOM_TOL ||
                Math.abs(n.size[0] - p.size[0]) > GEOM_TOL || Math.abs(n.size[1] - p.size[1]) > GEOM_TOL;
            dbg("pointerup moved =", moved, "geomChanged =", geomChanged);
            if (moved > CLICK_TOL || geomChanged) return;

            const { node, gx, gy } = findNode(e);
            const kind = node ? hitAt(node, gx, gy) : null;
            dbg("pointerup hit =", kind);
            if (node !== n || kind !== p.kind) return;

            if (kind === "preview") openFullscreen(imageSrc(node));
            else if (kind === "save") node.__sic.doSave();
            else if (kind === "folder") node.__sic.doFolder();
        },
        true
    );
}

// ---------------------------------------------------------------- extension
app.registerExtension({
    name: "SaveImageCopy.UI",

    setup() {
        installPointerHandlers();
    },

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const origCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origCreated?.apply(this, arguments);
            const node = this;
            node.properties = node.properties || {};

            // [💾 | 📁] one row, two halves
            const row = makeButtonRow(node);
            node.addCustomWidget(row);

            // backup_prefix field (node.properties -> not part of the prompt, no re-run on edit)
            // (old workflows stored it as properties.copy_path)
            const pathW = node.addWidget(
                "text",
                "backup_prefix",
                node.properties.backup_prefix ?? node.properties.copy_path ?? "",
                (v) => { node.properties.backup_prefix = v; }
            );
            noSerialize(row);
            noSerialize(pathW);

            const spacer = makeSpacer();
            node.addCustomWidget(spacer);

            node.__sic = {
                row,
                pathW,
                spacer,
                key: null,
                async doSave() {
                    try {
                        const data = await post(`${ROUTE}/copy_last`, {
                            node_id: nodeKey(node),
                            backup_prefix: pathW.value ?? "",
                        });
                        toast(data.ok, data.ok ? data.message : data.error || "Unknown error");
                    } catch (err) {
                        console.error("[SaveImageCopy] copy_last failed:", err);
                        toast(false, "request failed - " + err);
                    }
                },
                async doFolder() {
                    try {
                        const data = await post(`${ROUTE}/open_folder`, { node_id: nodeKey(node) });
                        if (!data.ok) toast(false, data.error || "Unknown error");
                    } catch (err) {
                        console.error("[SaveImageCopy] open_folder failed:", err);
                        toast(false, "request failed - " + err);
                    }
                },
            };

            ensureOrder(node);
            const sz = node.computeSize();
            node.setSize([Math.max(sz[0], node.size[0]), Math.max(sz[1], node.size[1])]);
        };

        const origConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            origConfigure?.apply(this, arguments);
            const pathW = this.__sic?.pathW;
            const saved = this.properties?.backup_prefix ?? this.properties?.copy_path;
            if (pathW && saved !== undefined) {
                pathW.value = saved;
            }
            ensureOrder(this);
        };

        const origExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            origExecuted?.apply(this, arguments);
            if (this.__sic && output?.sic_key?.length) {
                this.__sic.key = String(output.sic_key[0]);
            }
            ensureOrder(this);
        };

        // The preview widget can be added after execution; re-check order on draw.
        const origDraw = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function () {
            const r = origDraw?.apply(this, arguments);
            ensureOrder(this);
            return r;
        };
    },
});
