"""Construct pre-preservation fixtures; replay always runs without these shims."""
from contextlib import contextmanager
import deep_sky_siril_color as color
from deep_sky_siril_session import _PRE_VIVID_BINDINGS


@contextmanager
def legacy_color_producer(monkeypatch):
    read, render = color.load_json, color.script_text
    def old_read(path):
        value = read(path)
        if str(path).endswith('/session.json'):
            value={**value,'execution_policy':_PRE_VIVID_BINDINGS['execution_policy'].copy()}
        return value
    def old_script(root,source,run_id,parameters,**kwargs):
        for key in ('mask_policy',*color.SATURATION_KEYS):
            parameters.pop(key,None)
        return render(root,source,run_id,parameters,**kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(color,'load_json',old_read)
        scoped.setattr(color,'script_text',old_script)
        yield
