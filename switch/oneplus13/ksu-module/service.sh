#!/system/bin/sh
MODDIR=${0%/*}
while [ "$(getprop sys.user.0.ce_available)" != true ]; do
    sleep 2
done
exec "$MODDIR/op13-mount-shared" > "$MODDIR/shared-mount.log" 2>&1
