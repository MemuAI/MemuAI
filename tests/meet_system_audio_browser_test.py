"""Exercise the shipped MemuMeet sandbox and host recorder with synthetic audio."""
from pathlib import Path
import os
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest


@pytest.mark.parametrize("source,before_probe", [
    ("system", False), ("microphone", False), ("microphone", True),
], ids=["system", "switch-to-selected-microphone", "microphone-before-probe"])
def test_sandbox_records_native_audio_through_host(tmp_path, monkeypatch, source, before_probe):
    chromium = os.environ.get("MEMU_TEST_CHROMIUM", "")
    if not chromium or not Path(chromium).is_file():
        pytest.skip("Set MEMU_TEST_CHROMIUM to a Chromium executable")
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn

    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MEMUAI_FSINDEX_NOSTART", "1")
    monkeypatch.setenv("MEMUAI_MEET_NATIVE_BACKEND", "sine")
    monkeypatch.setenv("MEMUAI_PROXY_URL", "http://127.0.0.1:1")
    from app import meet_native_audio_api, meet_native_audio_helper, modules_host, pgdata
    from app.playground_api import create_app

    module_root = tmp_path / "modules"
    monkeypatch.setattr(meet_native_audio_helper, "choose_backend",
                        lambda _name: meet_native_audio_helper.SineBackend([
                            ("sdi:default", "Default microphone", 330.0, 48000, "input", False),
                            ("sine-a", "Virtual monitor A", 440.0, 48000),
                            ("sdi:sine", "Virtual microphone", 660.0, 48000, "input", False),
                        ]))
    monkeypatch.setattr(modules_host, "default_root", lambda: module_root)
    monkeypatch.setattr(pgdata, "load_preferences", lambda *args, **kwargs: {
        **pgdata.DEFAULT_PREFERENCES, "profile_onboarded": True, "profile_name": "Audio test",
    })
    modules_host.configure_module(module_root, "meet", enabled=True)
    modules_host.configure_module(module_root, "translator", enabled=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(create_app(), lifespan="off", log_level="error"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started
            with playwright.sync_playwright() as pw, pw.chromium.launch(executable_path=chromium) as browser:
                page = browser.new_page(viewport={"width": 1360, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda entry: errors.append(entry.text) if entry.type == "error" else None)
                page.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname == "127.0.0.1" else route.abort())
                first_probe = []
                sessions = []

                def observe_request(request):
                    if request.method == "POST" and request.url.endswith("/native-audio/sessions"):
                        sessions.append(request.post_data_json["devices"])

                def hold_first_probe(route):
                    if first_probe:
                        route.continue_()
                    else:
                        first_probe.append(route)

                page.on("request", observe_request)
                if before_probe:
                    page.route("**/native-audio/status", hold_first_probe)
                page.add_init_script("""(() => {
                  window.captureRequests = 0;
                  const createObjectURL = URL.createObjectURL.bind(URL);
                  URL.createObjectURL = blob => {
                    if (blob.type.startsWith('audio/')) window.testPreviewBlob = blob;
                    return createObjectURL(blob);
                  };
                  for (const method of ['getDisplayMedia', 'getUserMedia']) {
                    navigator.mediaDevices[method] = () => {
                      window.captureRequests++;
                      throw new Error('Browser capture must not be called');
                    };
                  }
                })();""")
                page.goto(base_url, wait_until="domcontentloaded")
                page.locator("body.boot-ready").wait_for()
                assert page.evaluate("window.captureRequests") == 0
                page.evaluate("() => MemuModules.open('translator')")
                translator = page.frame_locator('[data-module=translator] iframe')
                translator.locator("body script").first.wait_for(state="attached")
                assert translator.locator("body").evaluate("() => Memu.record.system('probe')") == {"reason": "unsupported"}
                assert translator.locator("body").evaluate("() => Memu.record.system('start')") == {"reason": "unsupported"}
                page.evaluate("() => MemuModules.closeAll()")
                page.evaluate("() => MemuModules.open('meet')")
                module = page.frame_locator('[data-module=meet] iframe')
                if not before_probe:
                    playwright.expect(module.locator("input[value=system]")).to_be_enabled()
                assert module.locator("body").evaluate("() => window.origin") == "null"
                module.locator("#tab-record").click()
                if not module.locator("#view-record .mm-step[data-step='1']").is_visible():
                    module.locator("#tab-record").click()
                switch_to_mic = source == "microphone" and not before_probe
                selected_source = "both" if switch_to_mic else source
                module.locator(f"input[value={selected_source}]").locator("..").click()
                check = "mic" if source == "microphone" else "system"
                if source == "microphone":
                    if before_probe:
                        playwright.expect(module.locator("#mic-device")).to_be_disabled()
                    else:
                        playwright.expect(module.locator("#mic-device")).to_be_enabled()
                        module.locator("#mic-device").select_option("sdi:sine")
                    page.evaluate("""() => {
                        window.testAudioContext = window.AudioContext;
                        window.AudioContext = class {
                          constructor() { throw new Error('Host audio graph is unavailable'); }
                        };
                    }""")
                else:
                    playwright.expect(module.locator("#native-devices").get_by_text("Virtual monitor A", exact=True)).to_be_visible()
                module.locator("#to-check").click()
                if switch_to_mic:
                    module.locator("#check-system").click()
                    playwright.expect(module.locator("#check-system-stop")).to_be_visible()
                playwright.expect(module.locator(f"#check-{check}-stop")).to_be_hidden()
                module.locator(f"#check-{check}").click()
                try:
                    playwright.expect(module.locator(f"#check-{check}-stop")).to_be_visible()
                except AssertionError as error:
                    raise AssertionError(f"{module.locator(f'#{check}-check-status').inner_text()}\n{errors}") from error
                if before_probe:
                    first_probe[0].continue_()
                    playwright.expect(module.locator("#mic-device")).to_be_enabled()
                if switch_to_mic:
                    playwright.expect(module.locator("#check-system-stop")).to_be_hidden()
                    playwright.expect(module.locator("#system-check-status")).to_be_empty()
                    assert sessions == [["sine-a"], ["sdi:sine"]]
                else:
                    assert sessions == [["sine-a" if source == "system" else "sdi:default"]]
                playwright.expect(module.locator(f"#level-{check}").locator("..")).not_to_have_attribute("aria-valuenow", "0")
                module.locator(f"#check-{check}-stop").click()
                if source == "microphone":
                    player = module.locator("#mic-test-player")
                    playwright.expect(player).to_be_visible()
                    assert player.get_attribute("src").startswith("blob:")
                    playwright.expect(player).to_have_js_property("error", None)
                    preview = player.evaluate("""async () => {
                        const ctx = new OfflineAudioContext(1, 1, 48000);
                        const audio = await ctx.decodeAudioData(await window.testPreviewBlob.arrayBuffer());
                        return audio.duration > 0 && audio.getChannelData(0).some(sample => Math.abs(sample) > .025);
                    }""")
                    assert preview
                    page.evaluate("() => { window.AudioContext = window.testAudioContext; }")
                if switch_to_mic:
                    # Continue with the same microphone-only recording gate.
                    module.locator("#back-sources").click()
                    module.locator("input[value=microphone]").locator("..").click()
                    module.locator("#to-check").click()
                module.locator("#to-record").click()
                module.locator("#rec-toggle").click()
                playwright.expect(module.locator("#rec-dot")).to_have_class("mm-rec-dot on")
                if source == "system":
                    playwright.expect(module.locator("#level-live-wrap")).not_to_have_attribute("aria-valuenow", "0")
                else:
                    # Allow enough speech for a short but independently decodable clip.
                    page.wait_for_timeout(350)
                module.locator("#rec-toggle").click()
                playwright.expect(module.locator("#pending-tracks audio")).to_be_visible()
                assert module.locator("#pending-tracks audio").get_attribute("src").startswith("data:audio/")
                if source == "microphone":
                    onset = module.locator("#pending-tracks audio").evaluate("""async el => {
                        const ctx = new AudioContext();
                        try {
                          const bytes = Uint8Array.from(atob(el.src.split(',')[1]), ch => ch.charCodeAt(0));
                          const audio = await ctx.decodeAudioData(bytes.buffer);
                          const onset = audio.getChannelData(0).findIndex(sample => Math.abs(sample) > .025);
                          return onset < 0 ? null : onset / audio.sampleRate;
                        } finally { await ctx.close(); }
                    }""")
                    assert onset is not None and onset < .2
                assert page.evaluate("window.captureRequests") == 0
                assert module.locator("body").evaluate("() => window.captureRequests") == 0
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            meet_native_audio_api.MANAGER.stop()
