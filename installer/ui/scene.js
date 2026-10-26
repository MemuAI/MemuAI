

(function () {
  "use strict";

  var VERT_QUAD = "attribute vec2 a_pos; void main(){ gl_Position = vec4(a_pos, 0.0, 1.0); }";

  var FRAG_STAR = [
    "precision highp float;",
    "uniform vec2 u_res; uniform vec2 u_center; uniform float u_time; uniform float u_scale;",
    "uniform vec2 u_tilt; uniform float u_spin; uniform float u_roll; uniform float u_energy; uniform float u_burst; uniform float u_light;",
    "uniform float u_cool; uniform float u_ringA; uniform float u_wave;",

    "uniform float u_ringR; uniform float u_px; uniform vec4 u_arcs; uniform float u_busy;",
    "const float F = 3.6;",

    "vec3 C1 = vec3(0.960, 0.540, 0.400);",
    "vec3 C2 = vec3(0.860, 0.440, 0.880);",
    "vec3 C3 = vec3(0.560, 0.520, 1.000);",
    "mat3 M;",
    "mat3 rotX(float a){ float c=cos(a), s=sin(a); return mat3(1.,0.,0., 0.,c,s, 0.,-s,c); }",
    "mat3 rotY(float a){ float c=cos(a), s=sin(a); return mat3(c,0.,-s, 0.,1.,0., s,0.,c); }",
    "mat3 rotZ(float a){ float c=cos(a), s=sin(a); return mat3(c,s,0., -s,c,0., 0.,0.,1.); }",
    "float smin(float a, float b, float k){ float h = clamp(.5 + .5*(b-a)/k, 0., 1.); return mix(b, a, h) - k*h*(1.-h); }",

    "float octa(vec3 p, vec3 r){ p = sqrt(p*p + 0.00012); return (p.x/r.x + p.y/r.y + p.z/r.z - 1.) / length(1./r); }",
    "float star(vec3 p){",
    "  float a = octa(p, vec3(1.00, 0.26, 0.25));",
    "  float b = octa(p, vec3(0.26, 1.00, 0.25));",
    "  float d = smin(a, b, 0.16);",
    "  d = smin(d, octa(p, vec3(0.38, 0.38, 0.34)), 0.07);",
    "  return d - 0.014;",
    "}",
    "float map(vec3 p){ return star(M * p / u_scale) * u_scale; }",
    "vec3 normalAt(vec3 p){",
    "  vec2 e = vec2(0.0015, -0.0015);",
    "  return normalize(e.xyy*map(p+e.xyy) + e.yyx*map(p+e.yyx) + e.yxy*map(p+e.yxy) + e.xxx*map(p+e.xxx));",
    "}",
    "float bar(vec3 d, vec3 c, vec3 a, float w, float cl){",
    "  c = normalize(c); vec3 side = normalize(cross(c, a));",
    "  float lat = abs(dot(d, side));",
    "  return smoothstep(w, w*0.35, lat) * smoothstep(cl - 0.04, cl + 0.03, dot(d, c));",
    "}",
    "vec3 env(vec3 d){",
    "  vec3 col = mix(vec3(0.012,0.010,0.020), vec3(0.070,0.052,0.090), smoothstep(-1., 1., d.y));",
    "  col += C1 * 1.4 * pow(max(dot(d, normalize(vec3(-0.65, 0.62, 0.45))), 0.), 10.);",
    "  col += C2 * 1.3 * pow(max(dot(d, normalize(vec3(0.72, -0.34, 0.52))), 0.), 8.);",
    "  col += C3 * 0.6 * pow(max(dot(d, normalize(vec3(0.1, -0.9, -0.4))), 0.), 5.);",

    "  col += vec3(1.0, 0.93, 0.90) * 3.0 * bar(d, vec3(-0.45, 0.78, 0.45), vec3(1.0, 0.25, 0.0), 0.045, 0.86);",
    "  col += mix(C2, vec3(1.0), 0.35) * 2.4 * bar(d, vec3(0.70, 0.12, 0.70), vec3(0.0, 1.0, 0.1), 0.05, 0.84);",
    "  col += mix(C1, vec3(1.0), 0.25) * 2.2 * bar(d, vec3(-0.25, -0.62, 0.74), vec3(1.0, -0.1, 0.0), 0.055, 0.86);",
    "  col += vec3(1.0, 0.97, 1.0) * 2.6 * bar(d, vec3(0.20, 0.32, -0.93), vec3(0.3, 1.0, 0.0), 0.05, 0.84);",
    "  col += mix(C2, C3, 0.4) * 2.2 * bar(d, vec3(-0.55, -0.15, -0.82), vec3(1.0, 0.45, 0.0), 0.06, 0.82);",
    "  col += C1 * 1.8 * bar(d, vec3(0.6, -0.55, -0.58), vec3(0.2, 0.2, 1.0), 0.05, 0.84);",
    "  col += vec3(1.0, 0.97, 0.95) * 2.2 * smoothstep(0.985, 0.996, dot(d, normalize(vec3(0.25, 0.85, 0.62))));",
    "  return col * u_light;",
    "}",
    "vec3 iri(float x){ return 0.5 + 0.5*cos(6.2831*(vec3(0.0, 0.33, 0.67) + x)); }",

    "vec2 arcDist(vec2 d, float R, float s, float len){",
    "  float rel = fract(fract(atan(d.x, d.y) / 6.28318 + 1.0) - s + 1.0);",
    "  if (rel <= len) return vec2(abs(length(d) - R), rel / max(len, 1e-4));",
    "  float a0 = s * 6.28318, a1 = (s + len) * 6.28318;",
    "  float d0 = length(d - R * vec2(sin(a0), cos(a0))), d1 = length(d - R * vec2(sin(a1), cos(a1)));",
    "  return d0 < d1 ? vec2(d0, 0.0) : vec2(d1, 1.0);",
    "}",

    "vec4 arcPaint(vec2 d, float s, float len, float head){",
    "  vec2 a = arcDist(d, u_ringR, s, len);",
    "  float hw = 1.6 * u_px, aa = max(0.75, 0.6 * u_px);",
    "  float cov = clamp((hw - a.x) / aa + 0.5, 0.0, 1.0) * smoothstep(0.0, 0.006, len);",
    "  vec3 c = mix(C1, C2, smoothstep(0.0, 0.55, a.y));",
    "  c = mix(c, mix(C3, vec3(1.0), 0.15), smoothstep(0.5, 1.0, a.y));",
    "  float a1 = (s + len) * 6.28318;",
    "  float hd = length(d - u_ringR * vec2(sin(a1), cos(a1)));",
    "  float glint = head * exp(-pow(hd / (3.2 * u_px), 2.0)) * smoothstep(0.004, 0.02, len);",
    "  float bloom = exp(-pow(a.x / (5.5 * u_px), 2.0)) * smoothstep(0.0, 0.006, len);",
    "  vec3 col = mix(c, vec3(1.0, 0.97, 1.0), glint * 0.85) * cov + c * bloom * 0.22 + vec3(1.0, 0.9, 1.0) * glint * 0.35;",
    "  return vec4(col, max(cov, glint * 0.6));",
    "}",
    "void main(){",
    "  C1 = mix(vec3(0.960, 0.540, 0.400), vec3(0.520, 0.600, 1.000), u_cool);",
    "  C2 = mix(vec3(0.860, 0.440, 0.880), vec3(0.700, 0.460, 1.000), u_cool);",
    "  C3 = mix(vec3(0.560, 0.520, 1.000), vec3(0.420, 0.820, 1.000), u_cool);",
    "  vec2 frag = gl_FragCoord.xy;",
    "  vec2 p = (frag - u_center) / (0.5 * u_res.y);",
    "  float t = u_time;",
    "  M = rotY(u_spin + u_tilt.x) * rotX(-u_tilt.y - 0.10*sin(t*0.37)) * rotZ(u_roll);",
    "  vec3 ro = vec3(0.0, 0.0, 4.0);",
    "  vec3 rd = normalize(vec3(p, -F));",

    "  float tc = -dot(ro, rd);",
    "  float dc = length(ro + rd*tc);",
    "  float R = 1.05 * u_scale;",
    "  vec3 glowCol = mix(C1, C2, smoothstep(-0.8, 0.8, p.x - p.y));",
    "  float pulse = 0.82 + 0.18*sin(t*1.7);",
    "  float halo = exp(-dc / u_scale * 2.4) * (0.30 + 0.55*u_energy) * pulse;",
    "  halo += exp(-dc / u_scale * 9.0) * 0.32 * (0.45 + u_energy);",

    "  halo += exp(-dc / u_scale * 1.15) * (0.11 + 0.16*u_energy) * pulse;",

    "  vec2 q = p / u_scale;",
    "  float fl = exp(-abs(q.y)*30.0) * exp(-abs(q.x)*1.6) + exp(-abs(q.x)*30.0) * exp(-abs(q.y)*2.2);",
    "  halo += fl * (0.13 + 0.45*u_energy + 0.9*u_burst) * pulse;",
    "  vec3 col = vec3(0.0); float alpha = 0.0;",

    "  float pixW = 4.0 * 2.0 / (u_res.y * F);",
    "  if (dc < R) {",
    "    float tt = max(tc - R, 0.0); bool hit = false; vec3 pos = ro + rd*tt;",
    "    float dmin = 1e3; float tmin = tt;",
    "    for (int i = 0; i < 80; i++) {",
    "      vec3 cp = ro + rd*tt; float d = map(cp);",
    "      if (d < dmin) { dmin = d; tmin = tt; }",
    "      if (d < 0.0006) { hit = true; break; }",
    "      tt += d * 0.9; if (tt > tc + R) break;",
    "    }",

    "    float cover = hit ? 1.0 : 1.0 - smoothstep(0.0, 1.25 * pixW, dmin);",
    "    pos = ro + rd * (hit ? tt : tmin);",
    "    if (cover > 0.002) {",
    "      vec3 n = normalAt(pos);",
    "      float ndv = clamp(dot(n, -rd), 0.0, 1.0);",
    "      float fres = 0.04 + 0.96*pow(1.0 - ndv, 5.0);",
    "      vec3 rf = reflect(rd, n);",
    "      vec3 film = iri(1.4*ndv + 0.25*dot(n, vec3(0.3,0.6,0.2)) + 0.03*t);",
    "      vec3 refl = env(rf) * mix(vec3(1.0), film, 0.55);",

    "      vec3 r2 = refract(rd, n, 1.0/1.47);",
    "      vec3 ip = pos - n*0.004; float th = 0.0; float core = 0.0;",
    "      for (int j = 0; j < 36; j++) {",
    "        float d = -map(ip);",
    "        vec3 lp = M * ip / u_scale;",
    "        core += exp(-length(lp)*5.5) * 0.06;",
    "        if (d < 0.0008 && j > 1) break;",
    "        float s = max(d, 0.006); ip += r2*s; th += s;",
    "      }",
    "      vec3 n2 = -normalAt(ip);",
    "      vec3 er = refract(r2, n2, 1.42); if (dot(er, er) < 0.01) er = reflect(r2, n2);",
    "      vec3 eg = refract(r2, n2, 1.47); if (dot(eg, eg) < 0.01) eg = reflect(r2, n2);",
    "      vec3 eb = refract(r2, n2, 1.53); if (dot(eb, eb) < 0.01) eb = reflect(r2, n2);",
    "      vec3 refr = vec3(env(er).r, env(eg).g, env(eb).b);",
    "      vec3 lpos = M * pos / u_scale;",
    "      vec3 tint = mix(C1, C2, smoothstep(-0.7, 0.7, lpos.x*0.7 - lpos.y*0.7));",
    "      refr = refr * mix(vec3(1.0), pow(tint, vec3(1.4))*1.6, 0.7) + tint * 0.05 * exp(-th / u_scale * 0.25) + tint * 0.08;",
    "      vec3 emis = tint * core * (1.0 + 2.4*u_energy) * pulse + vec3(1.0,0.9,0.95) * core * core * (0.25 + 0.8*u_energy);",
    "      col = mix(refr, refl, fres) + emis;",
    "      float spec = pow(max(dot(rf, normalize(vec3(0.25, 0.85, 0.62))), 0.0), 90.0);",
    "      col += vec3(1.0) * spec * 2.2 * u_light;",

    "      float lpx = 1.6 * pixW / u_scale;",
    "      float rw = max(1.0/70.0, lpx), mw = max(1.0/90.0, lpx);",
    "      float ridge = max(exp(-abs(lpos.x)/rw), exp(-abs(lpos.y)/rw)) * smoothstep(0.02, 0.2, length(lpos.xy)) * (1.0/70.0) / rw;",
    "      float rim = exp(-abs(lpos.z)/mw) * (1.0/90.0) / mw;",
    "      col += mix(vec3(1.0,0.92,0.9), film, 0.5) * (ridge*0.55 + rim*0.35) * (0.55 + 0.45*fres) * u_light;",
    "      col += tint * 0.05;",

    "      col += mix(C2, C3, 0.45) * pow(1.0 - ndv, 3.0) * (0.35 + 0.35*u_energy) * u_light;",

    "      float edge = 1.0 - smoothstep(0.0, 0.35, ndv);",
    "      col = mix(col, min(col, vec3(0.75)), edge * (hit ? 0.6 : 1.0));",
    "      col *= cover; alpha = cover;",
    "    }",
    "  }",
    "  col += glowCol * halo * (1.0 - alpha*0.7);",

    "  if (u_wave > 0.001) {",
    "    float wr = 1.0 + (1.0 - u_wave) * 2.4;",
    "    float w = exp(-pow((length(q) - wr) / (0.02 + 0.05*(1.0 - u_wave)), 2.0)) * u_wave;",
    "    vec3 wc = mix(C1, C2, 0.5) * w * 1.4 + vec3(1.0) * w * w * 0.6;",
    "    col += wc * (1.0 - alpha); alpha = max(alpha, clamp(w, 0.0, 1.0));",
    "  }",
    "  alpha = max(alpha, clamp(halo, 0.0, 1.0));",
    "  col = vec3(1.0) - exp(-col * 1.35);",
    "  col = pow(col, vec3(0.92));",

    "  if (u_ringA > 0.001) {",
    "    vec2 d = frag - u_center;",
    "    float aa = max(0.75, 0.6 * u_px);",
    "    float track = clamp((0.6 * u_px - abs(length(d) - u_ringR)) / aa + 0.5, 0.0, 1.0) * 0.12;",
    "    vec4 det = arcPaint(d, u_arcs.x, u_arcs.y, step(u_arcs.y, 0.999)) * (1.0 - u_busy);",
    "    vec4 spin = arcPaint(d, u_arcs.z, u_arcs.w, 1.0) * u_busy;",
    "    vec4 ring = vec4(vec3(1.0) * track, track) * (1.0 - max(det.a, spin.a));",
    "    ring += det + spin * (1.0 - det.a);",
    "    ring *= u_ringA;",
    "    col = ring.rgb + col * (1.0 - ring.a); alpha = ring.a + alpha * (1.0 - ring.a);",
    "  }",
    "  gl_FragColor = vec4(col, alpha);",
    "}"
  ].join("\n");

  var VERT_PTS = [
    "attribute vec4 a_seed; attribute float a_size;",
    "uniform vec2 u_res; uniform vec2 u_center; uniform float u_time; uniform float u_scale; uniform vec2 u_tilt;",
    "uniform float u_energy; uniform float u_progress; uniform float u_burst; uniform float u_side; uniform float u_dpr;",
    "uniform float u_flow; uniform float u_cool;",
    "varying float v_a; varying vec3 v_c;",
    "const float F = 3.6;",
    "mat3 rotX(float a){ float c=cos(a), s=sin(a); return mat3(1.,0.,0., 0.,c,s, 0.,-s,c); }",
    "mat3 rotZ(float a){ float c=cos(a), s=sin(a); return mat3(c,s,0., -s,c,0., 0.,0.,1.); }",
    "void main(){",
    "  float r = a_seed.x;",
    "  float sp = a_seed.w * (0.10 + 0.9*u_flow);",
    "  float ang = a_seed.y + u_time * sp;",

    "  float cyc = fract(a_seed.y*0.37 + u_time*0.06*(0.2 + 1.8*u_energy*u_progress));",
    "  float inflow = mix(1.0, mix(1.0, 0.25, cyc), clamp(u_energy*u_progress*1.4, 0.0, 1.0));",
    "  float rad = r * inflow * (1.0 + u_burst * (1.6 + 1.2*a_seed.z*a_seed.z));",
    "  vec3 p = vec3(cos(ang)*rad, a_seed.z*(0.18 + 0.28*r), sin(ang)*rad);",
    "  p = rotZ(-0.32 + u_tilt.x*0.6) * rotX(0.42 + u_tilt.y*0.6) * p;",
    "  p *= u_scale;",
    "  float front = step(0.0, p.z);",
    "  float keep = mix(1.0 - front, front, u_side);",
    "  float depth = 4.0 - p.z;",
    "  vec2 screen = u_center + p.xy * F / depth * 0.5 * u_res.y;",
    "  gl_Position = vec4(screen / u_res * 2.0 - 1.0, 0.0, 1.0);",
    "  gl_PointSize = max(1.0, a_size * u_dpr * (u_res.y/620.0) * (4.0/depth) * (1.0 + 0.6*u_burst));",
    "  float tw = 0.45 + 0.55*pow(abs(sin(u_time*(0.6 + a_seed.w) + a_seed.y*7.0)), 2.0);",
    "  float fade = smoothstep(0.0, 0.25, cyc) * (1.0 - u_burst*0.85);",
    "  float rim = smoothstep(0.32*u_scale, 0.9*u_scale, length(p.xy));",
    "  v_a = keep * tw * mix(1.0, fade, clamp(u_energy*u_progress*1.4,0.,1.)) * (0.35 + 0.65*rim) * (0.55 + 0.45*u_energy);",
    "  vec3 C1 = mix(vec3(0.937, 0.596, 0.490), vec3(0.560, 0.640, 1.000), u_cool);",
    "  vec3 C2 = mix(vec3(0.898, 0.560, 0.839), vec3(0.720, 0.540, 1.000), u_cool);",
    "  vec3 C3 = mix(vec3(0.620, 0.600, 1.000), vec3(0.480, 0.860, 1.000), u_cool);",
    "  v_c = mix(mix(C1, C2, fract(a_seed.y*0.1591)), C3, step(0.86, fract(a_seed.y*3.7)));",
    "}"
  ].join("\n");

  var FRAG_PTS = [
    "precision mediump float;",
    "varying float v_a; varying vec3 v_c;",
    "void main(){",
    "  vec2 d = gl_PointCoord - 0.5; float r = length(d);",
    "  float a = smoothstep(0.5, 0.0, r); a = a*a;",
    "  float core = smoothstep(0.18, 0.0, r);",
    "  gl_FragColor = vec4((v_c + core*0.8) * a * v_a, a * v_a);",
    "}"
  ].join("\n");

  function compile(gl, type, src) {
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      var log = gl.getShaderInfoLog(sh);
      gl.deleteShader(sh);
      throw new Error("shader: " + log);
    }
    return sh;
  }

  function program(gl, vs, fs) {
    var p = gl.createProgram();
    gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error("link: " + gl.getProgramInfoLog(p));
    var u = {}, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (var i = 0; i < n; i++) {
      var info = gl.getActiveUniform(p, i);
      u[info.name] = gl.getUniformLocation(p, info.name);
    }
    return { p: p, u: u };
  }

  function lerp(a, b, t) { return a + (b - a) * t; }

  var RING_RADIUS = 1.25;

  var Scene = {
    ok: false,
    reduced: false,
    _target: { x: 0.23, y: 0.47, scale: 0.47, light: 1 },
    _cur: { x: 0.23, y: 0.47, scale: 0.2, light: 1 },
    _energy: 0.15, _energyT: 0.15,
    _progress: 0, _progressT: 0,
    _flow: 0.35, _flowT: 0.35,
    _burst: 0, _burstStart: -1,
    _cool: 0, _ring: 0, _ringT: 0, _ringA: 0, _ringAT: 0, _busy: 0, _busyT: 0, _busyTurn: 0,
    _mouse: { x: 0, y: 0 }, _tilt: { x: 0, y: 0 },
    _spin: 0.6, _spinSpeed: 0.22, _roll: 0, _phase: 0,
    _frames: 0, _prev: 0,

    _q: 1, _qMin: 0.6, _ema: 0, _vsync: 0, _measureFrom: 0, _slowSince: 0, _fastSince: 0,
    _floorSince: 0, _raf: 0, _dirty: 3, _devW: 0, _devH: 0,

    init: function (canvas) {
      this.canvas = canvas;
      this.reduced = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

      var opts = { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false,
                   preserveDrawingBuffer: false, powerPreference: "high-performance" };
      var gl = null;
      try { gl = canvas.getContext("webgl", opts) || canvas.getContext("experimental-webgl", opts); } catch (e) { gl = null; }
      if (!gl) return false;

      try {
        var dbg = gl.getExtension("WEBGL_debug_renderer_info");
        var renderer = dbg ? String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)) : "";
        this.software = /swiftshader|llvmpipe|softpipe|basic render|warp|software/i.test(renderer);
      } catch (e) { this.software = false; }
      this.fullQuality = /[?&]quality=full/.test(location.search);
      this.snap = /[?&]shot=1/.test(location.search);
      this.adaptive = !this.fullQuality && !this.snap;
      try {
        this.gl = gl;
        this.star = program(gl, VERT_QUAD, FRAG_STAR);
        this.pts = program(gl, VERT_PTS, FRAG_PTS);
      } catch (err) {
        if (window.console) console.warn("MemuAI Setup: WebGL scene disabled —", err.message);
        return false;
      }
      this.quad = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.quad);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);

      var N = 520, seeds = new Float32Array(N * 4), sizes = new Float32Array(N), rnd = mulberry(7);
      for (var i = 0; i < N; i++) {
        var ring = rnd() < 0.72;
        seeds[i * 4] = ring ? 1.25 + rnd() * 0.85 : 0.9 + rnd() * 2.6;
        seeds[i * 4 + 1] = rnd() * Math.PI * 2;
        seeds[i * 4 + 2] = (rnd() - 0.5) * (ring ? 0.7 : 2.6);
        seeds[i * 4 + 3] = (0.25 + rnd() * 0.75) * (rnd() < 0.08 ? -1 : 1);
        sizes[i] = rnd() < 0.06 ? 4.5 + rnd() * 4 : 1.2 + rnd() * 2.4;
      }
      this.N = N;
      this.seedBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.seedBuf);
      gl.bufferData(gl.ARRAY_BUFFER, seeds, gl.STATIC_DRAW);
      this.sizeBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.sizeBuf);
      gl.bufferData(gl.ARRAY_BUFFER, sizes, gl.STATIC_DRAW);
      var self = this;
      window.addEventListener("resize", function () { self.resize(); });
      this._watchDpr();

      if (window.ResizeObserver) {
        try {
          var ro = new ResizeObserver(function (entries) {
            var e = entries[entries.length - 1], box = e.devicePixelContentBoxSize && e.devicePixelContentBoxSize[0];
            if (box) { self._devW = box.inlineSize; self._devH = box.blockSize; }
            self.resize();
          });
          try { ro.observe(canvas, { box: "device-pixel-content-box" }); } catch (err) { ro.observe(canvas); }
        } catch (err) {  }
      }

      document.addEventListener("visibilitychange", function () {
        if (document.hidden) self._stop(); else self._start();
      });
      window.addEventListener("mousemove", function (e) {
        self._mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
        self._mouse.y = (e.clientY / window.innerHeight) * 2 - 1;
      });
      document.addEventListener("mouseleave", function () { self._mouse.x = 0; self._mouse.y = 0; });
      canvas.addEventListener("webglcontextlost", function (e) { e.preventDefault(); self.ok = false; self._stop(); document.documentElement.classList.add("no-webgl"); });
      this.ok = true;
      this.resize();
      this._t0 = performance.now();
      this._last = this._t0;
      this._prev = this._t0;
      this._measureFrom = this._t0 + 1500;
      this._start();
      return true;
    },

    _start: function () {
      if (this._raf || !this.ok || document.hidden) return;
      var self = this;
      this._prev = this._last = performance.now();
      this._measureFrom = this._prev + 800;
      this._dirty = 3;
      this._raf = requestAnimationFrame(function loop(now) {
        self._raf = 0;
        if (!self.ok || document.hidden) return;
        self.frame(now);
        if (self.ok) self._raf = requestAnimationFrame(loop);
      });
    },
    _stop: function () { if (this._raf) { cancelAnimationFrame(this._raf); this._raf = 0; } },

    _watchDpr: function () {
      var self = this;
      if (!window.matchMedia) return;
      try {
        var mq = window.matchMedia("(resolution: " + (window.devicePixelRatio || 1) + "dppx)");
        var on = function () {
          if (mq.removeEventListener) mq.removeEventListener("change", on); else if (mq.removeListener) mq.removeListener(on);
          self._devW = 0; self._devH = 0;
          self.resize(); self._watchDpr();
        };
        if (mq.addEventListener) mq.addEventListener("change", on); else if (mq.addListener) mq.addListener(on);
      } catch (e) {  }
    },

    renderScale: function () {
      var dpr = window.devicePixelRatio || 1;
      var full = Math.min(Math.max(dpr, this.software ? 1 : 1.5), 2.5);
      var floor = this.software ? Math.min(dpr, 2.5) * 0.75 : Math.max(1, Math.min(dpr, 2.5) * 0.8);
      this._qMin = Math.min(1, floor / full);
      return Math.max(floor, full * this._q);
    },

    resize: function () {
      var scale = this.renderScale(), dpr = window.devicePixelRatio || 1;
      this.dpr = scale;
      var cw = this.canvas.clientWidth, ch = this.canvas.clientHeight;
      var w, h;
      if (this._devW && Math.abs(this._devW - cw * dpr) < 2.5) {
        w = Math.round(this._devW * scale / dpr); h = Math.round(this._devH * scale / dpr);
      } else { w = Math.round(cw * scale); h = Math.round(ch * scale); }
      w = Math.max(1, w); h = Math.max(1, h);
      if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
      this._dirty = 3;
    },

    _govern: function (now) {
      var gap = now - this._prev;
      this._prev = now;
      if (gap > 250 || gap <= 0) { this._measureFrom = now + 600; this._slowSince = this._fastSince = 0; return; }
      if (now < this._measureFrom) return;
      this._ema = this._ema ? this._ema * 0.92 + gap * 0.08 : gap;

      this._vsync = this._vsync ? Math.min(Math.max(this._vsync * 1.002, 5), Math.max(gap, 5)) : Math.max(gap, 5);
      var slow = this._ema > Math.max(this._vsync * 1.6, 22), fast = this._ema < this._vsync * 1.15 + 1.5;
      this._slowSince = slow ? (this._slowSince || now) : 0;
      this._fastSince = fast ? (this._fastSince || now) : 0;
      if (this._slowSince && now - this._slowSince > 1200) {
        if (this._q > this._qMin + 0.001) {
          this._q = Math.max(this._qMin, this._q - 0.15); this.resize();
          this._slowSince = 0; this._measureFrom = now + 500; this._ema = 0;
        } else if (this.software && this._ema > 70) {

          this._floorSince = this._floorSince || now;
          if (now - this._floorSince > 4000) { this.ok = false; this._stop(); document.documentElement.classList.add("no-webgl"); }
        }
      } else { this._floorSince = 0; }
      if (this._fastSince && now - this._fastSince > 4000 && this._q < 1) {
        this._q = Math.min(1, this._q + 0.1); this.resize();
        this._fastSince = 0; this._measureFrom = now + 500; this._ema = 0;
      }
    },

    setTarget: function (x, y, scale, light) {
      this._target.x = x; this._target.y = y; this._target.scale = scale;
      if (light != null) this._target.light = light;
      this._dirty = 3;
    },
    setEnergy: function (e) { this._energyT = Math.max(0, Math.min(1, e)); this._dirty = 3; },
    setProgress: function (p) { this._progressT = Math.max(0, Math.min(1, p)); this._dirty = 3; },
    setFlow: function (f) { this._flowT = f; this._dirty = 3; },

    setPalette: function (cool) { this._cool = Math.max(0, Math.min(1, cool)); this._dirty = 3; },

    setRing: function (value, visible, busy) {
      if (value != null) this._ringT = Math.max(0, Math.min(1, value));
      if (visible != null) this._ringAT = Math.max(0, Math.min(1, visible));
      if (busy != null) this._busyT = busy ? 1 : 0;
      this._dirty = 3;
    },
    burst: function () { if (this.reduced) return; this._burstStart = this._time != null ? this._time : (performance.now() - this._t0) / 1000; },

    frame: function (now) {
      if (!this.ok) return;
      var gl = this.gl;
      var dt = Math.max(0, Math.min(0.05, (now - this._last) / 1000));
      this._last = now;
      this._frames++;
      if (this.adaptive && !this.reduced) { this._govern(now); if (!this.ok) return; }

      if (this.reduced && this._dirty <= 0) return;
      if (this._dirty > 0) this._dirty--;
      var time = this.reduced ? 2.0 : this.snap ? 7.3 + (this._frames || 0) * 0.02 : (now - this._t0) / 1000;
      this._time = time;
      var k = this.reduced || this.snap ? 1 : 1 - Math.pow(0.0018, dt);
      var c = this._cur, tg = this._target;
      c.x = lerp(c.x, tg.x, k); c.y = lerp(c.y, tg.y, k); c.scale = lerp(c.scale, tg.scale, k); c.light = lerp(c.light, tg.light, k);
      this._energy = lerp(this._energy, this._energyT, this.reduced || this.snap ? 1 : 1 - Math.pow(0.05, dt));
      this._progress = lerp(this._progress, this._progressT, this.reduced || this.snap ? 1 : 1 - Math.pow(0.02, dt));
      this._flow = lerp(this._flow, this._flowT, this.reduced || this.snap ? 1 : 1 - Math.pow(0.1, dt));
      var rk = this.reduced || this.snap ? 1 : 1 - Math.pow(0.004, dt);
      this._ring = this._ringT < this._ring - 0.02 ? this._ringT : lerp(this._ring, this._ringT, rk);

      this._ringA = lerp(this._ringA, this._ringAT, this.reduced || this.snap ? 1 : 1 - Math.pow(this._ringAT > this._ringA ? 0.02 : 0.0004, dt));
      this._busy = lerp(this._busy, this._busyT, this.reduced || this.snap ? 1 : 1 - Math.pow(0.003, dt));
      if (!this.reduced) this._busyTurn = (this._busyTurn + dt * 0.8) % 1;
      var mk = 1 - Math.pow(0.04, dt);
      this._tilt.x = lerp(this._tilt.x, this.reduced ? 0 : this._mouse.x * 0.35, mk);
      this._tilt.y = lerp(this._tilt.y, this.reduced ? 0 : this._mouse.y * 0.25, mk);

      this._spinSpeed = 0.35 + this._energy * 1.4 * this._flow;
      if (!this.reduced) {
        this._phase += dt * (0.32 + this._energy * 0.25);
        this._roll += dt * this._energy * this._flow * 0.9;
      }
      this._spin = this.reduced ? 0.42 : 0.5 * Math.sin(this._phase) + 0.12;
      var burst = 0;
      if (this._burstStart >= 0) {
        var bt = time - this._burstStart;
        burst = bt < 0 ? 0 : Math.max(0, 1 - bt / 1.6);
        burst = burst * burst * (3 - 2 * burst);
        if (bt > 1.8) this._burstStart = -1;
      }
      var W = this.canvas.width, H = this.canvas.height;
      var cx = c.x * W, cy = (1 - c.y) * H;
      var scale = c.scale * (1 + 0.03 * Math.sin(time * 0.8)) * (1 + burst * 0.08);
      gl.viewport(0, 0, W, H);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.enable(gl.BLEND);

      this._points(0, time, cx, cy, scale, burst);

      var s = this.star;
      gl.useProgram(s.p);
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.quad);
      var loc = gl.getAttribLocation(s.p, "a_pos");
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      gl.uniform2f(s.u.u_res, W, H);
      gl.uniform2f(s.u.u_center, cx, cy);
      gl.uniform1f(s.u.u_time, time);
      gl.uniform1f(s.u.u_scale, scale);
      gl.uniform2f(s.u.u_tilt, this._tilt.x, this._tilt.y);
      gl.uniform1f(s.u.u_spin, this._spin);
      gl.uniform1f(s.u.u_roll, this._roll + 0.07 * Math.sin(time * 0.45));
      gl.uniform1f(s.u.u_energy, this._energy);
      gl.uniform1f(s.u.u_burst, burst);
      gl.uniform1f(s.u.u_light, c.light);
      gl.uniform1f(s.u.u_cool, this._cool);
      gl.uniform1f(s.u.u_ringA, this._ringA);

      gl.uniform1f(s.u.u_ringR, RING_RADIUS * c.scale * 0.5 * H);
      gl.uniform1f(s.u.u_px, this.dpr);
      gl.uniform4f(s.u.u_arcs, 0, this._ring, this._busyTurn, 0.17 + 0.07 * Math.sin(time * 1.9));
      gl.uniform1f(s.u.u_busy, this._busy);
      gl.uniform1f(s.u.u_wave, this._burstStart >= 0 ? burst : 0);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.disableVertexAttribArray(loc);
      this._points(1, time, cx, cy, scale, burst);
    },

    _points: function (side, time, cx, cy, scale, burst) {
      var gl = this.gl, P = this.pts;
      gl.useProgram(P.p);
      gl.blendFunc(gl.ONE, gl.ONE);
      var a1 = gl.getAttribLocation(P.p, "a_seed"), a2 = gl.getAttribLocation(P.p, "a_size");
      gl.bindBuffer(gl.ARRAY_BUFFER, this.seedBuf);
      gl.enableVertexAttribArray(a1);
      gl.vertexAttribPointer(a1, 4, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.sizeBuf);
      gl.enableVertexAttribArray(a2);
      gl.vertexAttribPointer(a2, 1, gl.FLOAT, false, 0, 0);
      gl.uniform2f(P.u.u_res, this.canvas.width, this.canvas.height);
      gl.uniform2f(P.u.u_center, cx, cy);
      gl.uniform1f(P.u.u_time, time);
      gl.uniform1f(P.u.u_scale, scale);
      gl.uniform2f(P.u.u_tilt, this._tilt.x, this._tilt.y);
      gl.uniform1f(P.u.u_energy, this._energy);
      gl.uniform1f(P.u.u_progress, this._progress);
      gl.uniform1f(P.u.u_burst, burst);
      gl.uniform1f(P.u.u_side, side);
      gl.uniform1f(P.u.u_dpr, this.dpr);
      gl.uniform1f(P.u.u_flow, this._flow);
      gl.uniform1f(P.u.u_cool, this._cool);
      gl.drawArrays(gl.POINTS, 0, this.N);
      gl.disableVertexAttribArray(a1);
      gl.disableVertexAttribArray(a2);
    }
  };

  function mulberry(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  Scene.RING_RADIUS = RING_RADIUS;
  window.MemuScene = Scene;
})();
