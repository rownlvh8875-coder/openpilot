// Exercise the actual browser module with deterministic media/DOM boundaries.
// No network, browser installation, route log, or hardware is required.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../radar_view.js', import.meta.url), 'utf8');

function harness({videoDuration = 0, payload} = {}) {
  const canvases = [], callbacks = new Map();
  let now = 0, nextFrame = 0, response = payload;
  class Element {
    constructor(tag) {
      this.tag = tag; this.children = []; this.attrs = {}; this.events = new Map();
      this.value = ''; this.textContent = ''; this.width = 0; this.height = 0;
      this.clientWidth = 640; this.clientHeight = 350;
      this.classList = {toggle() {}, add() {}, remove() {}};
      if (tag === 'canvas') canvases.push(this);
    }
    append(...children) {
      for (const child of children) {
        if (child.parent) child.parent.children = child.parent.children.filter(x => x !== child);
        this.children.push(child); child.parent = this;
      }
    }
    before(element) { this.parent.append(element); }
    closest(tag) { return this.tag === tag ? this : this.parent?.closest(tag); }
    set innerHTML(html) {
      this.children = [];
      for (const match of html.matchAll(/<([a-z]+)\b([^>]*)>/g)) {
        const element = new Element(match[1]);
        for (const attr of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) element.attrs[attr[1]] = attr[2] ?? '';
        element.className = element.attrs.class || '';
        if ('data-sensor' in element.attrs) element.value = 'auto';
        if ('data-range' in element.attrs) element.value = '130';
        this.append(element);
      }
    }
    querySelector(selector) {
      const matches = element => selector.startsWith('.') ? (element.className || '').split(' ').includes(selector.slice(1))
        : selector.startsWith('[') ? selector.slice(1, -1) in element.attrs : element.tag === selector;
      for (const child of this.children) {
        if (matches(child)) return child;
        const found = child.querySelector(selector); if (found) return found;
      }
      return null;
    }
    addEventListener(name, handler) { this.events.set(name, [...(this.events.get(name) || []), handler]); }
    dispatch(name) { for (const handler of this.events.get(name) || []) handler(); }
    getAttribute(name) { return this.attrs[name] ?? null; }
    getBoundingClientRect() { return {left: 0, top: 0, width: this.clientWidth, height: this.clientHeight}; }
    getContext() {
      if (!this.context) {
        this.context = {
          strokes: [], path: [], setTransform() {}, clearRect() {}, fillText() {}, fillRect() {}, strokeRect() {},
          setLineDash() {}, arc() {}, fill() {}, drawImage() {},
          beginPath() { this.path = []; },
          moveTo(x, y) { this.path.push(['move', x, y]); },
          lineTo(x, y) { this.path.push(['line', x, y]); },
          stroke() { this.strokes.push({color: this.strokeStyle, path: this.path.slice()}); },
        };
      }
      return this.context;
    }
  }
  const document = {head: new Element('head'), body: new Element('body'), createElement: tag => new Element(tag)};
  const videoSection = new Element('section'), video = new Element('video');
  document.body.append(videoSection); videoSection.append(video);
  video.duration = videoDuration; video.currentTime = 0; video.paused = true; video.ended = false; video.playCalls = 0;
  if (videoDuration) video.attrs.src = '/synthetic-video';
  video.play = async () => {
    video.playCalls++; video.ended = false;
    if (video.paused) { video.paused = false; video.dispatch('play'); }
  };
  video.pause = () => { if (!video.paused) { video.paused = true; video.dispatch('pause'); } };
  video.finish = () => {
    video.currentTime = video.duration; video.ended = true; video.paused = true;
    video.dispatch('timeupdate'); video.dispatch('pause'); video.dispatch('ended');
  };
  const context = vm.createContext({
    document, window: {addEventListener() {}}, location: {pathname: '/synthetic-route'},
    devicePixelRatio: 1, performance: {now: () => now},
    requestAnimationFrame: callback => { callbacks.set(++nextFrame, callback); return nextFrame; },
    cancelAnimationFrame: id => callbacks.delete(id), ResizeObserver: class {observe() {}},
    AbortController, URLSearchParams, DOMException, setTimeout, clearTimeout,
    fetch: async () => ({status: 200, ok: true, json: async () => structuredClone(response)}),
  });
  vm.runInContext(source.replace('export function attachRadarReview', 'function attachRadarReview'), context);
  const review = context.attachRadarReview(video);
  const find = selector => document.body.querySelector(selector);
  return {
    video, find, canvases,
    load: async next => { if (next) response = next; await review.load({index: 0, name: 'synthetic', videoUrl: videoDuration ? '/synthetic-video' : null}); },
    advance: milliseconds => { now += milliseconds; const pending = [...callbacks.values()]; callbacks.clear(); for (const callback of pending) callback(now); },
    seek: time => { find('.radar-scrub').value = String(time); find('.radar-scrub').oninput(); },
    play: () => find('[data-toggle-play]').onclick(),
    current: () => Number(find('.radar-scrub').value),
  };
}

function data(times, {aligned = false, graphs = {}} = {}) {
  return {schemaVersion: 1, videoAligned: aligned, sensor: 'front', sensitivity: 3, sourceVersion: 'synthetic', graphs,
    frames: times.map(time => ({time_s: time, video_time_s: aligned ? time : null, path: [], points: [], model_leads: [], selection: {}}))};
}

test('radar-only playback pauses, seeks and restarts without touching video', async () => {
  const h = harness({videoDuration: 1, payload: data([0, .1, .2])});
  await h.load(); h.play(); h.advance(100);
  assert.equal(h.current(), .1); assert.equal(h.video.currentTime, 0); assert.equal(h.video.playCalls, 0);
  h.play(); h.advance(100); assert.equal(h.current(), .1);
  h.seek(.2); h.play(); assert.equal(h.current(), 0);
  h.advance(200); assert.equal(h.current(), .2);
  assert.equal(h.find('[data-toggle-play]').textContent, '재생');
});

test('short aligned video hands playback to radar and marks the unsynchronized tail', async () => {
  const h = harness({videoDuration: .1, payload: data([0, .1, .2, .3], {aligned: true})});
  await h.load(); h.play(); h.video.finish();
  assert.equal(h.current(), .1); assert.match(h.find('[data-clock-status]').textContent, /레이더만 별도로/);
  h.advance(100); assert.equal(h.current(), .2);
  h.advance(100); assert.equal(h.current(), .3);
  assert.match(h.find('[data-clock-status]').textContent, /레이더만 별도로/);
  h.play(); assert.equal(h.current(), 0); assert.equal(h.video.paused, false);
  assert.equal(h.find('[data-clock-status]').textContent, '');
});

test('seeking between video and radar tail preserves the requested pause or play state', async () => {
  const h = harness({videoDuration: .1, payload: data([0, .1, .2, .3], {aligned: true})});
  await h.load(); h.play(); h.seek(.2);
  assert.equal(h.video.currentTime, .1); assert.equal(h.video.paused, true);
  h.advance(50); assert.equal(h.current(), .25);
  h.play(); h.seek(.05); assert.equal(h.video.paused, true);
  h.advance(50); assert.equal(h.current(), .05);
  h.play(); h.seek(.06); h.video.finish(); h.advance(100);
  assert.equal(h.current(), .2);
});

test('video playback stops at the end of available paired radar evidence', async () => {
  const h = harness({videoDuration: 1, payload: data([0, .1, .2], {aligned: true})});
  await h.load(); h.play(); h.video.currentTime = .3; h.advance(300);
  assert.equal(h.current(), .2); assert.equal(h.video.paused, true);
});

test('graphs do not connect across null values or unmapped timestamps', async () => {
  const h = harness({payload: data([0, .05, .1, .15], {graphs: {leadOne: [
    {color: '#test', samples: [[0, 10], [.05, null], [.1, 20], [999, 40], [.15, 30]]},
  ]}})});
  await h.load();
  const stroke = h.canvases.at(-1).getContext().strokes.findLast(item => item.color === '#test');
  assert.deepEqual(stroke.path.map(point => point[0]), ['move', 'move', 'move']);
});

test('segment reload clears a running radar tail and stale graph data', async () => {
  const h = harness({videoDuration: .1, payload: data([0, .1, .2, .3], {aligned: true})});
  await h.load(); h.play(); h.video.finish(); h.advance(100);
  await h.load(data([0, .05])); h.advance(100);
  assert.equal(h.current(), 0); assert.equal(h.find('[data-clock-status]').textContent, '');
  assert.equal(h.find('[data-toggle-play]').textContent, '재생');
  h.play(); h.advance(50); assert.equal(h.current(), .05);
});
