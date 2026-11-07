#!/system/bin/sh
SKIPUNZIP=0
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/service.sh" 0 0 0755
set_perm "$MODPATH/op13-switch-android" 0 0 0755
set_perm "$MODPATH/op13-mount-shared" 0 0 0755
