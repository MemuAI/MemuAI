"""Native appearance regressions using fake DWM/WinForms objects only."""
import ctypes
import types
import unittest
import os
from unittest.mock import Mock, patch

from app import desktop, module_appbar, win_backdrop, win_titlebar, win_window_theme as theme
from tests.win_titlebar_test import make_controller, HWND

class NativeSurface(unittest.TestCase):
    def form(self):
        control=types.SimpleNamespace(DefaultBackgroundColor="#ffffff")
        return types.SimpleNamespace(BackColor="#ffffff",browser=types.SimpleNamespace(webview=control),webview=control)

    def test_colorref_keeps_the_actual_background_not_rgb_byte_order(self):
        self.assertEqual(theme.colorref("#123456"),0x563412)
        self.assertEqual(theme.background("white",False),"#f7f6f3")
        self.assertEqual(theme.background("#A1B2C3"),"#a1b2c3")

    def test_light_to_dark_updates_dwm_border_and_caption_even_with_system_light(self):
        attributes={34:0xffffff}
        def setter(key,value): attributes[key]=value;return True
        self.assertTrue(theme.apply_frame(HWND,False,"#fbfaf8",setter=setter))
        self.assertEqual(attributes[34],theme.colorref("#fbfaf8"))
        self.assertTrue(theme.apply_frame(HWND,True,"#17171a",setter=setter))
        self.assertEqual(attributes[20],1)
        self.assertEqual(attributes[34],theme.colorref("#17171a"))
        self.assertEqual(attributes[35],theme.colorref("#17171a"))
        self.assertEqual(attributes[36],0xf4f4f4)

    def test_windows_10_rejecting_new_colour_attributes_keeps_dark_fallback(self):
        calls=[]
        def setter(key,value): calls.append((key,value));return key==19
        self.assertTrue(theme.apply_frame(HWND,True,setter=setter))
        self.assertEqual(calls[:2],[(20,1),(19,1)])
        self.assertTrue(any(key==34 for key,_ in calls))

    def test_surface_changes_both_form_and_webview_fallback_colours(self):
        native=self.form()
        self.assertTrue(theme.apply_surface(native,True,"#08080b",color_factory=str))
        self.assertEqual(native.BackColor,"#08080b")
        self.assertEqual(native.webview.DefaultBackgroundColor,"#08080b")

        native.width=1001;native.webview.width=1000
        self.assertEqual(native.BackColor,native.webview.DefaultBackgroundColor)
        theme.apply_surface(native,False,"#f5f4f8",color_factory=str)
        self.assertEqual(native.BackColor,"#f5f4f8")

    def test_material_transparency_is_preserved_and_restored_to_current_theme(self):
        native=self.form()
        theme.apply_surface(native,True,"#101018",transparent=True,color_factory=str)
        self.assertEqual((native.BackColor,native.webview.DefaultBackgroundColor),("#000000","transparent"))
        theme.apply_surface(native,True,"#101018",transparent=False,color_factory=str)
        self.assertEqual((native.BackColor,native.webview.DefaultBackgroundColor),("#101018","#101018"))

    def test_missing_native_controls_never_fail(self):
        self.assertFalse(theme.apply_surface(None,True,color_factory=str))
        self.assertTrue(theme.apply_surface(types.SimpleNamespace(),True,color_factory=str))
        self.assertFalse(theme.apply_frame(0,True,setter=Mock()))

    def test_material_none_repaints_surface_after_legacy_original_background_restore(self):
        native=self.form()
        controller=win_backdrop.BackdropController(lambda:HWND,lambda fn:fn(),native,build=22631)
        controller._webview_transparent=Mock(side_effect=lambda _:setattr(native,"BackColor","#ffffff"))
        fake_dwm=types.SimpleNamespace(DwmExtendFrameIntoClientArea=Mock(return_value=0),DwmSetWindowAttribute=Mock(return_value=0))
        original=theme.apply_surface
        with patch.object(ctypes,"windll",types.SimpleNamespace(dwmapi=fake_dwm)), \
             patch.object(theme,"apply_frame",return_value=True), \
             patch.object(theme,"apply_surface",side_effect=lambda *a,**k:original(*a,**k,color_factory=str)):
            result=controller.apply("none",True,"#08080b")
        self.assertEqual(result["active"],"none")
        self.assertEqual((native.BackColor,native.webview.DefaultBackgroundColor),("#08080b","#08080b"))

    def test_detached_module_theme_runs_on_ui_thread_and_keeps_own_background(self):
        native=self.form();invoked=[]
        controller=types.SimpleNamespace(native_surface=native,_hwnd_provider=lambda:HWND,
            _invoke=lambda fn:(invoked.append("ui"),fn())[1])
        bridge=module_appbar.ModuleAppbarBridge(controller,"music",{})
        with patch.object(theme,"apply",return_value=True) as apply:
            self.assertTrue(bridge.appbar_theme(True,"#060609"))
        apply.assert_called_once_with(native,HWND,True,"#060609")
        self.assertEqual(controller.native_theme,(True,"#060609"))
        self.assertEqual(invoked,["ui"])

    def test_recreated_native_handle_reapplies_saved_theme_without_changing_geometry(self):
        controller,api=make_controller();controller.native_theme=(True,"#060609")
        controller.native_surface=self.form()
        with patch.object(theme,"apply",return_value=True) as apply:
            self.assertTrue(controller.attach(lambda:HWND))
        apply.assert_called_once_with(controller.native_surface,HWND,True,"#060609",transparent=False)
        self.assertEqual(api.rect,(100,50,1100,850))

    def test_desktop_initial_theme_does_not_override_later_theme_when_shown_again(self):
        shell=desktop.DesktopShell("http://offline",background_color="#09090b")
        native=self.form();shell.window=types.SimpleNamespace(native=native)
        with patch("app.win_app_identity.native_hwnd",return_value=HWND), \
             patch.object(theme,"apply",return_value=True) as apply:
            shell._apply_native_theme()
            shell._apply_native_theme()
        apply.assert_called_once_with(native,HWND,True,"#09090b")

    @unittest.skipUnless(os.name=="nt","MemuFiles native path is Windows-only")
    def test_files_theme_updates_native_surface_on_both_theme_changes(self):
        from app.memufiles.desktop import Desktop
        native=self.form();app=Desktop(None,None,"light")
        with patch.object(app,"_native",return_value=native),patch.object(app,"hwnd",return_value=HWND), \
             patch.object(theme,"apply",return_value=True) as apply:
            app.apply_theme("dark");app.apply_theme("light")
        self.assertEqual(apply.call_args_list[0].args,(native,HWND,True,"#17171a"))
        self.assertEqual(apply.call_args_list[1].args,(native,HWND,False,"#f7f6f3"))

    def test_notes_theme_updates_native_surface_on_both_theme_changes(self):
        from app.memunote.desktop import Desktop
        native=self.form();app=Desktop(None,None,"light")
        with patch.object(app,"_native",return_value=native),patch.object(app,"hwnd",return_value=HWND), \
             patch.object(theme,"apply",return_value=True) as apply:
            app.apply_theme("dark");app.apply_theme("light")
        self.assertEqual(apply.call_args_list[0].args,(native,HWND,True,"#1a1a1c"))
        self.assertEqual(apply.call_args_list[1].args,(native,HWND,False,"#fbfaf8"))

if __name__=="__main__":unittest.main()
