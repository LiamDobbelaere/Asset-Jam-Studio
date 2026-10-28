// GLB viewer on three.js (loaded from the CDN through the import map in index.html).
// Plays glTF animations (rigged characters from the Animate page): load() returns the clip names,
// playClip(name) / stopClip() drive the mixer, onClips(cb) is called when a model with clips is loaded.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export class Viewer {
  constructor(container) {
    this.el = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x1d2130);
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x444466, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(3, 5, 4); this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xbfd0ff, 0.8); fill.position.set(-4, 2, -3); this.scene.add(fill);
    this.grid = new THREE.GridHelper(2, 20, 0x3a4060, 0x2a2f42);
    this.scene.add(this.grid);
    this.model = null;
    this.wire = false;
    this.alive = true;
    this.mixer = null; this.clips = []; this.action = null; this.clock = new THREE.Clock();
    this.skeleton = null; this.showBones = false;
    this.tools = document.createElement('div'); this.tools.className = 'vtools';
    this.info = document.createElement('div'); this.info.className = 'vinfo'; this.info.textContent = 'loading…';
    container.append(this.tools, this.info);
    const mk = (label, fn) => { const b = document.createElement('button'); b.className = 'btn xs'; b.textContent = label; b.onclick = fn; this.tools.appendChild(b); return b; };
    mk('Wireframe', () => this.setWire(!this.wire));
    this.bonesBtn = mk('Bones', () => this.setBones(!this.showBones)); this.bonesBtn.style.display = 'none';
    mk('Reset view', () => this.fit());
    this._ro = new ResizeObserver(() => this.resize());
    this._ro.observe(container);
    this.resize();
    this._loop = () => {
      if (!this.alive) return;
      const dt = this.clock.getDelta();
      if (this.mixer) this.mixer.update(dt);
      this.controls.update(); this.renderer.render(this.scene, this.camera); requestAnimationFrame(this._loop);
    };
    requestAnimationFrame(this._loop);
  }
  resize() {
    const w = this.el.clientWidth || 600, h = this.el.clientHeight || 500;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
  }
  async load(url) {
    this.clear();
    this.info.textContent = 'loading…';
    const gltf = await new GLTFLoader().loadAsync(url);
    if (!this.alive) return;
    this.model = gltf.scene;
    let tris = 0, verts = 0, skinned = false;
    this.model.traverse(o => {
      if (o.isMesh) {
        const g = o.geometry; verts += g.attributes.position.count;
        tris += g.index ? g.index.count / 3 : g.attributes.position.count / 3;
        o.userData.mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of o.userData.mats) { m.side = THREE.DoubleSide; }
        if (o.isSkinnedMesh) { skinned = true; o.frustumCulled = false; }
      }
    });
    this.scene.add(this.model);
    this.clips = gltf.animations || [];
    if (this.clips.length) { this.mixer = new THREE.AnimationMixer(this.model); }
    if (skinned) { this.skeleton = new THREE.SkeletonHelper(this.model); this.skeleton.visible = this.showBones; this.scene.add(this.skeleton); this.bonesBtn.style.display = ''; }
    this.fit();
    this.info.textContent = `${Math.round(tris).toLocaleString()} tris · ${verts.toLocaleString()} verts` + (this.clips.length ? ` · ${this.clips.length} clip${this.clips.length > 1 ? 's' : ''}` : '');
    this.setWire(this.wire);
    return { tris: Math.round(tris), verts, clips: this.clips.map(c => ({ name: c.name, duration: c.duration })) };
  }
  playClip(name) {
    if (!this.mixer) return;
    const clip = this.clips.find(c => c.name === name) || this.clips[0];
    if (!clip) return;
    if (this.action) this.action.stop();
    this.action = this.mixer.clipAction(clip);
    this.action.reset().setLoop(THREE.LoopRepeat, Infinity).play();
    return clip;
  }
  stopClip() { if (this.action) { this.action.stop(); this.action = null; } if (this.mixer) this.mixer.stopAllAction(); }
  setBones(on) { this.showBones = on; if (this.skeleton) this.skeleton.visible = on; }
  fit() {
    if (!this.model) return;
    const box = new THREE.Box3().setFromObject(this.model);
    const size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z) || 1;
    this.grid.scale.setScalar(maxDim);
    this.grid.position.set(center.x, box.min.y, center.z);
    const dist = maxDim / (2 * Math.tan((this.camera.fov * Math.PI) / 360)) * 1.35;
    this.camera.position.set(center.x + dist * 0.8, center.y + dist * 0.55, center.z + dist * 0.8);
    this.camera.near = maxDim / 100; this.camera.far = maxDim * 100; this.camera.updateProjectionMatrix();
    this.controls.target.copy(center); this.controls.update();
  }
  snapshot(size = 320) {
    // render once at the thumbnail size and read the pixels back in the same tick
    const w = this.el.clientWidth || size, h = this.el.clientHeight || size;
    this.renderer.setSize(size, size, false);
    this.camera.aspect = 1; this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
    const url = this.renderer.domElement.toDataURL('image/png');
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    return url;
  }
  setWire(on) {
    this.wire = on;
    if (!this.model) return;
    this.model.traverse(o => { if (o.isMesh) for (const m of o.userData.mats || []) m.wireframe = on; });
  }
  clear() {
    this.stopClip(); this.mixer = null; this.clips = [];
    if (this.skeleton) { this.scene.remove(this.skeleton); this.skeleton.dispose?.(); this.skeleton = null; this.bonesBtn.style.display = 'none'; }
    if (this.model) { this.scene.remove(this.model); this.model.traverse(o => { if (o.isMesh) { o.geometry.dispose(); } }); this.model = null; }
  }
  dispose() { this.alive = false; this._ro.disconnect(); this.clear(); this.renderer.dispose(); this.el.innerHTML = ''; }
}
