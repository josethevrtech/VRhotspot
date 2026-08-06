(function installVrhotspotOnlyDeveloperHub() {
'use strict';
const PATHS = {
status: '/v1/status',
devices: '/v1/devbridge/adb/devices',
wireless: '/v1/devbridge/adb/enable-wireless',
tools: '/v1/devbridge/tools/status',
toolsRemove: '/v1/devbridge/tools/remove',
};
const state = {
ready: false,
busy: false,
starting: false,
complete: false,
manualJoin: false,
manualSsid: '',
manualNote: '',
finishing: false,
advanceTimer: null,
usbSerial: '',
usbDevices: [],
usbTimer: null,
joinTimer: null,
toolsBusy: false,
};
const el = (id) => document.getElementById(id);
const text = (node, value) => {
if (node && node.textContent !== value) node.textContent = value;
};
const normalize = (value) => String(value || '')
.replace(/_/g, ' ')
.replace(/\s+/g, ' ')
.trim();
function publicResult(response) {
return response && response.json && response.json.data
? response.json.data
: {};
}
function resultData(response) {
const result = publicResult(response);
return result && result.data && typeof result.data === 'object'
? result.data
: {};
}
function resultCode(response) {
const result = publicResult(response);
return String(
result.result_code
|| (response && response.json && response.json.result_code)
|| (response ? `HTTP ${response.status}` : 'request_failed'),
);
}
function resultMessage(response, fallback) {
const result = publicResult(response);
return String(
result.message
|| (response && response.json && response.json.message)
|| fallback,
);
}
async function call(path, options = {}) {
if (typeof api !== 'function') {
return { ok: false, status: 503, json: null, raw: '' };
}
return api(path, options);
}
function feedback(message, kind = 'idle') {
const node = el('devhubFeedback');
text(node, message || '');
if (node) node.dataset.state = kind;
}
function statePayload(response) {
const data = response && response.json && response.json.data;
if (!data || typeof data !== 'object') return {};
return data.data && typeof data.data === 'object' ? data.data : data;
}
async function runningStatus() {
const response = await call(PATHS.status);
const runtime = statePayload(response);
return {
response,
runtime,
running: !!(
response.ok
&& runtime
&& (runtime.running || runtime.phase === 'running')
),
};
}
function stopTimer(name) {
if (state[name]) window.clearInterval(state[name]);
state[name] = null;
}
function stopAdvance() {
if (state.advanceTimer) window.clearTimeout(state.advanceTimer);
state.advanceTimer = null;
}
function chosenUsb() {
return state.usbDevices.find((device) => String(device.serial || '') === state.usbSerial)
|| state.usbDevices.find((device) => device.state === 'device')
|| state.usbDevices[0]
|| null;
}
function modelOf(device) {
const props = device && device.properties && typeof device.properties === 'object'
? device.properties
: {};
return normalize(props.model || props.product || 'Android XR headset');
}
async function loadUsbDevices() {
const response = await call(PATHS.devices);
if (!response.ok) throw new Error(resultMessage(response, 'Unable to inspect ADB devices.'));
const data = resultData(response);
state.usbDevices = (Array.isArray(data.devices) ? data.devices : []).filter((device) => {
const serial = String(device.serial || '');
return serial && !serial.includes(':');
});
if (!state.usbDevices.some((device) => String(device.serial || '') === state.usbSerial)) {
const preferred = state.usbDevices.find((device) => device.state === 'device')
|| state.usbDevices[0];
state.usbSerial = preferred ? String(preferred.serial || '') : '';
}
}
function setStep(step) {
document.querySelectorAll('#devhubWirelessWizard .devhub-wizard-step').forEach((node) => {
const value = Number(node.dataset.step);
node.classList.toggle('current', value === step);
node.classList.toggle('complete', value < step);
});
}
function wizardCopy(title, detail) {
text(el('devhubWizardTitle'), title);
text(el('devhubWizardDetail'), detail);
}
function renderUsb(device) {
const host = el('devhubWizardDevice');
if (!host) return;
host.replaceChildren();
host.hidden = !device;
if (!device) return;
const name = document.createElement('strong');
name.textContent = modelOf(device);
const serial = document.createElement('span');
serial.className = 'devhub-sensitive-identifier';
serial.textContent = String(device.serial || '');
host.append(name, serial);
if (state.usbDevices.length > 1) {
const select = document.createElement('select');
select.id = 'devhubWizardUsbSelect';
for (const candidate of state.usbDevices) {
const option = document.createElement('option');
option.value = String(candidate.serial || '');
option.textContent = `${modelOf(candidate)} — ${candidate.state || 'unknown'}`;
option.selected = option.value === state.usbSerial;
select.appendChild(option);
}
select.addEventListener('change', () => {
state.usbSerial = select.value;
renderWizard();
});
host.appendChild(select);
}
}
function manualJoinPanel(show) {
const panel = el('devhubManualJoin');
if (panel) panel.hidden = !show;
}
function manualJoinStatus(message) {
text(el('devhubManualJoinStatus'), message);
}
function renderManualJoin(device) {
const ssid = state.manualSsid || 'VRhotspot';
setStep(3);
wizardCopy(
`Put on your headset and join ${ssid}`,
'Horizon OS does not permit automatic Wi-Fi enrollment, so this step '
+ `happens inside the headset: open Settings → Wi-Fi and select ${ssid}. `
+ 'Keep the USB cable connected the whole time.',
);
text(el('devhubManualJoinSsid'), ssid);
if (!state.busy) {
manualJoinStatus(
state.manualNote || `Waiting for ${modelOf(device)} to join ${ssid}…`,
);
}
manualJoinPanel(true);
}
function renderWizard() {
const action = el('devhubWizardAction');
const device = chosenUsb();
if (!action) return;
renderUsb(device);
if (state.finishing) {
manualJoinPanel(false);
setStep(4);
action.disabled = true;
action.textContent = 'Enabling wireless…';
return;
}
if (state.complete) {
manualJoinPanel(false);
setStep(5);
action.disabled = false;
action.textContent = 'Done';
return;
}
if (state.manualJoin) {
const usbState = device ? String(device.state || '').toLowerCase() : '';
action.textContent = 'I’ve joined — check again';
if (usbState !== 'device') {
stopTimer('joinTimer');
manualJoinPanel(false);
setStep(3);
wizardCopy(
'USB cable disconnected',
'Reconnect the USB cable to the headset. Developer Hub paused the '
+ 'VRhotspot network checks and will resume once USB is back.',
);
action.disabled = true;
return;
}
renderManualJoin(device);
if (!state.joinTimer) startManualPolling();
action.disabled = state.busy;
return;
}
manualJoinPanel(false);
action.textContent = 'Join VRhotspot & enable wireless';
if (!device) {
setStep(1);
wizardCopy(
'Connect the headset by USB',
'Use a data-capable USB cable. Developer Hub will detect the headset automatically.',
);
action.disabled = true;
return;
}
const adbState = String(device.state || '').toLowerCase();
if (adbState === 'unauthorized') {
setStep(2);
wizardCopy(
'Approve USB debugging inside the headset',
'Enable “Always allow from this computer,” then select Allow.',
);
action.disabled = true;
return;
}
if (adbState !== 'device') {
setStep(1);
wizardCopy(
'Waiting for the USB headset',
`The headset reports “${adbState || 'unknown'}.” Reconnect it and keep it awake.`,
);
action.disabled = true;
return;
}
setStep(3);
wizardCopy(
`${modelOf(device)} is ready to join VRhotspot`,
'Wireless ADB remains disabled until the headset address and route match VRhotspot.',
);
action.disabled = state.busy;
}
async function pollUsb() {
const modal = el('devhubWirelessWizard');
if (!modal || modal.hidden || state.busy) return;
try {
await loadUsbDevices();
renderWizard();
} catch (error) {
wizardCopy('Unable to inspect USB devices', String(error.message || error));
}
}
function startUsbPolling() {
stopTimer('usbTimer');
void pollUsb();
state.usbTimer = window.setInterval(pollUsb, 1600);
}
function selectWirelessTarget(target) {
el('devhubRefresh')?.click();
let attempt = 0;
const timer = window.setInterval(() => {
attempt += 1;
const row = Array.from(document.querySelectorAll('#devhubDeviceList .devhub-list-item'))
.find((candidate) => {
if (candidate.dataset.devhubSerial === target) return true;
const address = candidate.querySelector('.devhub-device-address');
return address && String(address.textContent || '').includes(target);
});
if (row) {
row.querySelector('button')?.click();
window.clearInterval(timer);
} else if (attempt >= 10) {
window.clearInterval(timer);
}
}, 450);
}
function startManualPolling() {
stopTimer('joinTimer');
state.joinTimer = window.setInterval(() => {
if (state.manualJoin && !state.busy) void bootstrapWireless(true);
}, 2200);
}
async function bootstrapWireless(background = false) {
if (state.complete) {
closeWizard();
return;
}
if (state.busy || state.finishing) return;
const device = chosenUsb();
if (!device || device.state !== 'device') return;
state.busy = true;
renderWizard();
if (!background && !state.manualJoin) {
setStep(4);
wizardCopy(
'Verifying the VRhotspot network',
'Developer Hub will not enable wireless ADB on another network.',
);
} else if (!background && state.manualJoin) {
manualJoinStatus(
`Checking whether the headset joined ${state.manualSsid || 'VRhotspot'}…`,
);
}
try {
const response = await call(PATHS.wireless, {
method: 'POST',
body: JSON.stringify({
serial: String(device.serial || ''),
port: 5555,
}),
});
const result = publicResult(response);
const data = resultData(response);
const code = resultCode(response);
if (response.ok && result.success) {
state.manualJoin = false;
state.finishing = true;
stopTimer('joinTimer');
manualJoinPanel(false);
setStep(4);
wizardCopy(
`${normalize(data.model || modelOf(device))} joined ${data.ssid || 'VRhotspot'}`,
'Enabling wireless ADB through the VRhotspot network…',
);
const doneTitle = `${normalize(data.model || modelOf(device))} is connected through VRhotspot`;
const doneDetail = `USB can be disconnected. Wireless ADB is using ${data.ssid || 'the dedicated VRhotspot network'}.`;
const target = data.target ? String(data.target) : '';
stopAdvance();
state.advanceTimer = window.setTimeout(() => {
state.advanceTimer = null;
state.finishing = false;
state.complete = true;
setStep(5);
wizardCopy(doneTitle, doneDetail);
feedback('Wireless headset setup completed through VRhotspot.', 'success');
if (target) selectWirelessTarget(target);
renderWizard();
}, 900);
return;
}
if (code === 'hotspot_not_running') {
closeWizard();
showPrecondition();
return;
}
if (code === 'hotspot_configuration_incomplete') {
closeWizard();
showPrecondition({
configurationError: true,
message: resultMessage(response, 'The hotspot configuration is incomplete.'),
});
return;
}
if (
code === 'wifi_control_unavailable'
|| data.requires_manual_join === true
|| code === 'headset_not_on_vrhotspot'
|| code === 'headset_address_outside_hotspot_subnet'
) {
const ssid = String(data.ssid || state.manualSsid || 'VRhotspot');
const alreadyManual = state.manualJoin;
state.manualJoin = true;
state.manualSsid = ssid;
state.manualNote = '';
if (!alreadyManual) {
feedback(`Waiting for the headset to join ${ssid}.`, 'loading');
}
return;
}
if (background && state.manualJoin) {
state.manualNote = `The last check did not complete (${code}). Retrying automatically…`;
return;
}
state.manualJoin = false;
stopTimer('joinTimer');
setStep(3);
wizardCopy('Wireless setup needs attention', resultMessage(response, code));
feedback(resultMessage(response, code), 'error');
} catch (error) {
if (background && state.manualJoin) {
state.manualNote = 'Unable to reach VRhotspot. Retrying automatically…';
return;
}
state.manualJoin = false;
stopTimer('joinTimer');
setStep(3);
wizardCopy('Wireless setup needs attention', String(error.message || error));
feedback(String(error.message || error), 'error');
} finally {
state.busy = false;
renderWizard();
}
}
function ensureDialogs() {
if (!el('devhubWirelessWizard')) {
document.body.insertAdjacentHTML('beforeend', `
<div id="devhubWirelessWizard" class="devhub-wizard-overlay" hidden
role="dialog" aria-modal="true" aria-labelledby="devhubWizardHeading">
<div class="devhub-wizard-dialog">
<div class="devhub-wizard-header">
<h2 id="devhubWizardHeading">Connect headset to VRhotspot</h2>
<button id="devhubWizardClose" class="btn sm secondary" type="button">Close</button>
</div>
<div class="devhub-wizard-steps">
${['Connect USB', 'Approve debugging', 'Join VRhotspot', 'Enable wireless', 'Complete']
.map((label, index) => `<div class="devhub-wizard-step" data-step="${index + 1}"><span>${index + 1}</span><strong>${label}</strong></div>`)
.join('')}
</div>
<div class="devhub-wizard-body">
<h3 id="devhubWizardTitle"></h3>
<p id="devhubWizardDetail"></p>
<div id="devhubManualJoin" class="devhub-manual-join" hidden>
<div class="devhub-manual-join-wait">
<span class="devhub-manual-join-spinner" aria-hidden="true"></span>
<span id="devhubManualJoinStatus" role="status" aria-live="polite"></span>
</div>
<ol class="devhub-manual-join-steps">
<li>Put on the headset and open <strong>Settings → Wi-Fi</strong>.</li>
<li>Select <strong id="devhubManualJoinSsid" class="devhub-manual-join-ssid"></strong> and join it.</li>
<li>Keep the USB cable connected the whole time.</li>
</ol>
<p class="devhub-manual-join-note">Horizon OS does not permit automatic Wi-Fi enrollment. Developer Hub keeps checking automatically and continues as soon as the headset is on VRhotspot.</p>
</div>
<p class="devhub-wizard-policy">Developer Hub will not expose wireless ADB through another Wi-Fi network.</p>
<div id="devhubWizardDevice" class="devhub-wizard-device" hidden></div>
</div>
<div class="devhub-wizard-footer">
<button id="devhubWizardCancel" class="btn secondary" type="button">Cancel</button>
<button id="devhubWizardAction" class="btn primary" type="button" disabled>Join VRhotspot &amp; enable wireless</button>
</div>
</div>
</div>`);
el('devhubWizardClose').addEventListener('click', closeWizard);
el('devhubWizardCancel').addEventListener('click', closeWizard);
el('devhubWizardAction').addEventListener('click', () => void bootstrapWireless(false));
el('devhubWirelessWizard').addEventListener('click', (event) => {
if (event.target === el('devhubWirelessWizard')) closeWizard();
});
}
if (!el('devhubHotspotPrecondition')) {
document.body.insertAdjacentHTML('beforeend', `
<div id="devhubHotspotPrecondition" class="devhub-wizard-overlay" hidden
role="dialog" aria-modal="true" aria-labelledby="devhubHotspotPreconditionTitle">
<div class="devhub-wizard-dialog devhub-precondition-dialog">
<div class="devhub-wizard-header">
<h2 id="devhubHotspotPreconditionTitle">Start VRhotspot first</h2>
</div>
<div class="devhub-wizard-body">
<div class="devhub-precondition-copy">
<p><strong>Wireless headset setup only works through the dedicated VRhotspot network.</strong></p>
<p>Start the hotspot before continuing. Developer Hub will never enable wireless ADB through another Wi-Fi network.</p>
</div>
<div id="devhubHotspotPreconditionStatus" class="devhub-precondition-status"
role="status" aria-live="polite"></div>
</div>
<div class="devhub-wizard-footer">
<button id="devhubHotspotCancel" class="btn secondary" type="button">Cancel</button>
<button id="devhubOpenHotspotSetup" class="btn secondary" type="button" hidden>Open Hotspot Setup</button>
<button id="devhubStartHotspot" class="btn primary" type="button">Start Hotspot</button>
</div>
</div>
</div>`);
el('devhubHotspotCancel').addEventListener('click', closePrecondition);
el('devhubOpenHotspotSetup').addEventListener('click', navigateToSetup);
el('devhubStartHotspot').addEventListener('click', () => void startFromPrecondition());
}
document.addEventListener('keydown', (event) => {
if (event.key !== 'Escape') return;
if (!el('devhubWirelessWizard').hidden) closeWizard();
else if (!el('devhubHotspotPrecondition').hidden) closePrecondition();
});
}
function openWizard() {
state.complete = false;
state.manualJoin = false;
state.manualSsid = '';
state.manualNote = '';
state.finishing = false;
state.busy = false;
state.usbDevices = [];
state.usbSerial = '';
stopAdvance();
manualJoinPanel(false);
el('devhubWirelessWizard').hidden = false;
document.body.classList.add('devhub-modal-open');
renderWizard();
startUsbPolling();
el('devhubWizardClose').focus();
}
function closeWizard() {
el('devhubWirelessWizard').hidden = true;
document.body.classList.remove('devhub-modal-open');
stopTimer('usbTimer');
stopTimer('joinTimer');
stopAdvance();
state.finishing = false;
el('devhubWirelessSetup')?.focus();
}
function showPrecondition(options = {}) {
const configurationError = options.configurationError === true;
const status = el('devhubHotspotPreconditionStatus');
text(status, String(options.message || ''));
status.dataset.state = configurationError ? 'error' : '';
el('devhubStartHotspot').hidden = configurationError;
el('devhubStartHotspot').disabled = false;
text(el('devhubStartHotspot'), 'Start Hotspot');
el('devhubOpenHotspotSetup').hidden = !configurationError;
el('devhubHotspotPrecondition').hidden = false;
document.body.classList.add('devhub-modal-open');
(configurationError ? el('devhubOpenHotspotSetup') : el('devhubStartHotspot')).focus();
}
function closePrecondition() {
el('devhubHotspotPrecondition').hidden = true;
document.body.classList.remove('devhub-modal-open');
el('devhubWirelessSetup')?.focus();
}
async function waitForRunning(timeoutMs = 30000) {
const deadline = Date.now() + timeoutMs;
while (Date.now() <= deadline) {
const checked = await runningStatus();
if (checked.running) return true;
await new Promise((resolve) => window.setTimeout(resolve, 700));
}
return false;
}
async function startFromPrecondition() {
if (state.starting) return;
state.starting = true;
const start = el('devhubStartHotspot');
const open = el('devhubOpenHotspotSetup');
const status = el('devhubHotspotPreconditionStatus');
start.disabled = true;
text(start, 'Starting…');
text(status, 'Starting VRhotspot through the existing hotspot workflow…');
open.hidden = true;
try {
if (typeof startHotspot === 'function') {
await startHotspot(null, 'Developer Hub');
} else {
const button = el('btnStart') || el('btnStartBasic');
if (!button) throw new Error('Hotspot setup is not available.');
button.click();
}
if (!(await waitForRunning(30000))) {
throw new Error('VRhotspot did not reach the running state.');
}
closePrecondition();
openWizard();
} catch (error) {
text(status, String(error.message || error));
status.dataset.state = 'error';
start.hidden = true;
open.hidden = false;
} finally {
state.starting = false;
}
}
function navigateToSetup() {
closePrecondition();
const nav = Array.from(document.querySelectorAll('.nav-item')).find((item) =>
String(item.textContent || '').toLowerCase().includes('set up hotspot')
) || document.querySelector('.nav-item[data-tab="status"]');
nav?.click();
window.setTimeout(() => (el('btnStartBasic') || el('btnStart') || el('ssid'))?.focus(), 0);
}
async function beginSetup() {
if ((await runningStatus()).running) openWizard();
else showPrecondition();
}
function card(panel, title) {
return Array.from(panel.querySelectorAll(':scope > .devhub-workspace-grid > .card'))
.find((node) => node.querySelector('.card-header h2')?.textContent.trim() === title);
}
function restructureWorkspace(workspace) {
const devicePanel = workspace.querySelector('.devhub-workspace-panel[data-view="device"]');
const connectionPanel = workspace.querySelector('.devhub-workspace-panel[data-view="connection"]');
if (!devicePanel || !connectionPanel) return false;
const devices = card(devicePanel, 'ADB Devices');
const discovery = card(connectionPanel, 'Network Discovery');
const pairing = card(connectionPanel, 'Wireless Pairing');
const connect = card(connectionPanel, 'Connect Headset');
if (!devices || !discovery || !pairing || !connect) return false;
devices.querySelector('.card-header h2').textContent = 'Headsets';
const header = devices.querySelector('.card-header');
header.classList.add('devhub-headsets-header');
const actions = document.createElement('div');
actions.className = 'devhub-headsets-actions';
const setup = document.createElement('button');
setup.id = 'devhubWirelessSetup';
setup.type = 'button';
setup.className = 'btn sm primary';
setup.textContent = 'Set up wireless headset';
setup.addEventListener('click', () => void beginSetup());
const disconnect = el('devhubDisconnect');
if (disconnect && disconnect.parentNode === header) {
header.insertBefore(actions, disconnect);
actions.append(setup, disconnect);
} else {
actions.appendChild(setup);
header.appendChild(actions);
}
const connectionQuickAction = Array.from(
workspace.querySelectorAll('.devhub-quick-action'),
).find((item) => item.querySelector('strong')?.textContent.trim() === 'Connection');
if (connectionQuickAction) {
const oldButton = connectionQuickAction.querySelector('button');
if (oldButton) {
const replacement = oldButton.cloneNode(true);
const label = replacement.querySelector('strong');
if (label) label.textContent = 'Set up wireless headset';
replacement.addEventListener('click', () => void beginSetup());
oldButton.replaceWith(replacement);
}
const tip = connectionQuickAction.querySelector('.devhub-quick-action-tip');
if (tip) {
const help = 'Connect and authorize a headset only through the dedicated VRhotspot network.';
tip.setAttribute('data-tip', help);
tip.setAttribute('aria-label', help);
}
}
const candidates = el('devhubCandidateList');
if (candidates) {
const section = document.createElement('section');
section.id = 'devhubUnifiedDiscovery';
section.className = 'devhub-unified-discovery';
section.innerHTML = '<div class="devhub-unified-section-title"><h3>Detected on VRhotspot</h3></div>';
section.appendChild(candidates);
devices.querySelector('.card-body').appendChild(section);
}
discovery.remove();
pairing.remove();
connect.remove();
el('devhubAdvancedConnection')?.remove();
const connectionTab = workspace.querySelector(
'.devhub-workspace-tab[data-view="connection"]',
);
const connectionWasActive = !!(
connectionTab && connectionTab.classList.contains('active')
);
connectionTab?.remove();
connectionPanel.remove();
try {
if (sessionStorage.getItem('vrhs_devhub_workspace_view') === 'connection') {
sessionStorage.setItem('vrhs_devhub_workspace_view', 'device');
}
} catch { }
if (connectionWasActive) {
workspace.querySelector('.devhub-workspace-tab[data-view="device"]')?.click();
}
return true;
}
function cleanCandidates() {
const section = el('devhubUnifiedDiscovery');
const rows = section?.querySelectorAll('.devhub-list-item') || [];
if (section) section.hidden = rows.length === 0;
rows.forEach((row) => {
row.querySelector('button')?.remove();
const meta = row.querySelector('.devhub-list-meta');
if (meta && !meta.textContent.includes('Use USB setup')) {
text(meta, `${meta.textContent} · Use USB setup to authorize`);
}
});
}
function cleanApps() {
const pathInput = el('devhubApkPath');
if (pathInput) {
pathInput.required = false;
const container = pathInput.closest('.devhub-host-path') || pathInput.closest('div');
container?.remove();
}
const serial = el('devhubPackageSerial');
if (serial) {
serial.required = false;
serial.hidden = true;
serial.tabIndex = -1;
serial.setAttribute('aria-hidden', 'true');
const label = document.querySelector('label[for="devhubPackageSerial"]');
if (label) label.hidden = true;
}
document.querySelectorAll('#tab-devhub .small.faded').forEach((node) => {
if (String(node.textContent || '').toLowerCase().includes('path must exist on the linux machine')) {
node.remove();
}
});
el('devhubAdvancedConnection')?.remove();
if (typeof companionAuthBridgeAvailable === 'function' && companionAuthBridgeAvailable()) {
const choose = document.querySelector('#devhubApkPicker .btn');
const drop = document.querySelector('#devhubApkPicker .devhub-file-drop');
const meta = document.querySelector('#devhubApkPicker .devhub-file-meta');
if (choose) choose.disabled = true;
if (drop) {
drop.tabIndex = -1;
drop.removeAttribute('role');
drop.setAttribute('aria-disabled', 'true');
}
text(
meta,
'APK file upload is temporarily unavailable in the desktop companion. Open the browser Web Portal to deploy an APK.',
);
}
}
function normalizeDeviceStates() {
document.querySelectorAll('#devhubDeviceList .devhub-list-item').forEach((row) => {
const node = row.querySelector('.devhub-device-state');
if (!node) return;
const raw = String(row.dataset.devhubState || node.textContent || '').trim().toLowerCase();
if (raw === 'device') {
node.hidden = true;
node.setAttribute('aria-hidden', 'true');
text(node, 'device');
return;
}
node.hidden = false;
node.removeAttribute('aria-hidden');
text(node, {
unauthorized: 'Approve USB debugging',
offline: 'Offline',
recovery: 'Recovery mode',
}[raw] || 'Unavailable');
});
}
function toolsModel(response) {
const outer = publicResult(response);
const status = outer.adb ? outer : (outer.data && outer.data.adb ? outer.data : {});
const adb = status.adb && typeof status.adb === 'object' ? status.adb : {};
return {
source: String(adb.source || 'missing'),
managed: adb.managed && typeof adb.managed === 'object' ? adb.managed : {},
system: adb.system && typeof adb.system === 'object' ? adb.system : {},
};
}
function bindToolsRemoveButton() {
const current = el('devhubRemoveTools');
if (!current || current.dataset.vrhotspotRemoveBound === '1') return current;
const replacement = current.cloneNode(true);
replacement.dataset.vrhotspotRemoveBound = '1';
replacement.addEventListener('click', async () => {
const source = String(replacement.dataset.toolsSource || 'managed');
const adbPath = String(replacement.dataset.adbPath || '');
if (source === 'system') {
const confirmed = window.confirm(
`Uninstall the operating-system package that provides ${adbPath || 'adb'}? `
+ 'Other Android development tools on this computer may also use it.',
);
if (!confirmed) return;
}
replacement.disabled = true;
feedback(
source === 'system'
? 'Uninstalling the system ADB package...'
: 'Removing VRhotspot-managed Android Platform-Tools...',
'loading',
);
try {
const response = await call(PATHS.toolsRemove, {
method: 'POST',
body: JSON.stringify({ source, path: adbPath }),
});
const result = publicResult(response);
if (!response.ok || result.success !== true) {
feedback(resultMessage(response, resultCode(response)), 'error');
return;
}
feedback(result.message || 'ADB removal completed.', 'success');
el('devhubRefresh')?.click();
await updateTools();
} catch (error) {
feedback(String(error.message || error), 'error');
} finally {
replacement.disabled = false;
}
});
current.replaceWith(replacement);
return replacement;
}
async function updateTools() {
const section = el('devhubToolsManagement');
if (!section || state.toolsBusy) return;
state.toolsBusy = true;
try {
const response = await call(PATHS.tools);
if (!response.ok) return;
const model = toolsModel(response);
const install = el('devhubInstallTools');
const remove = bindToolsRemoveButton();
const license = el('devhubAcceptToolsLicense')?.closest('.devhub-checks');
const status = document.querySelector('#tab-devhub .devhub-card-status .small');
const broken = model.managed.present === true && model.managed.verified === false;
const managed = model.source === 'managed'
&& model.managed.installed === true
&& model.managed.verified !== false;
section.hidden = false;
if (broken) {
install.hidden = false;
text(install, 'Repair Managed ADB');
remove.hidden = false;
remove.dataset.toolsSource = 'managed';
remove.dataset.adbPath = String(model.managed.path || '');
text(remove, 'Remove Managed ADB');
if (license) license.hidden = false;
text(status, 'Managed ADB needs repair');
} else if (managed) {
install.hidden = false;
text(install, 'Reinstall Managed ADB');
remove.hidden = false;
remove.dataset.toolsSource = 'managed';
remove.dataset.adbPath = String(model.managed.path || '');
text(remove, 'Remove Managed ADB');
if (license) license.hidden = false;
text(status, 'Managed ADB ready');
} else if (model.source === 'system') {
section.hidden = false;
install.hidden = true;
remove.hidden = false;
remove.dataset.toolsSource = 'system';
remove.dataset.adbPath = String(model.system.path || '');
text(remove, 'Uninstall System ADB');
if (license) license.hidden = true;
text(status, 'System ADB ready');
} else {
install.hidden = false;
text(install, 'Install Managed ADB');
remove.hidden = true;
remove.dataset.toolsSource = 'managed';
remove.dataset.adbPath = '';
if (license) license.hidden = false;
text(status, 'ADB unavailable');
}
} finally {
state.toolsBusy = false;
}
}
function reconcile() {
cleanCandidates();
cleanApps();
normalizeDeviceStates();
const empty = el('devhubDeviceList')?.querySelector('.devhub-empty');
text(empty, empty ? 'No headsets are connected. Use “Set up wireless headset” to add one.' : '');
void updateTools();
}
function inject() {
if (state.ready) return true;
const workspace = el('devhubWorkspace');
if (!workspace || !restructureWorkspace(workspace)) return false;
ensureDialogs();
state.ready = true;
document.documentElement.dataset.devhubVrhotspotOnlyReady = '1';
const observer = new MutationObserver(reconcile);
observer.observe(workspace, { childList: true, subtree: true });
reconcile();
window.setInterval(() => {
const pane = el('tab-devhub');
if (pane?.classList.contains('active') && !document.hidden) reconcile();
}, 5000);
return true;
}
function start() {
if (inject()) return;
const observer = new MutationObserver(() => {
if (inject()) observer.disconnect();
});
observer.observe(document.documentElement, { childList: true, subtree: true });
}
if (document.readyState === 'loading') {
document.addEventListener('DOMContentLoaded', start, { once: true });
} else {
start();
}
})();
