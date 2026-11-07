/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Exercise the real client's GIO/D-Bus calls, signals and cleanup; no QRTR. */
#include <gio/gio.h>
#include <math.h>
#include <string.h>
static GMainLoop *loop;static GDBusConnection *connection;
static guint claims,releases,ticks;static gchar *owner;static gboolean wrong_owner,bad,flat;
static GSubprocess *child;
static const gchar xml[]="<node>"
 "<interface name='net.hadess.SensorProxy'>"
 "<method name='ClaimAccelerometer'/><method name='ReleaseAccelerometer'/>"
 "<method name='ClaimGyroscope'/><method name='ReleaseGyroscope'/>"
 "<property name='HasAccelerometer' type='b' access='read'/>"
 "<property name='HasGyroscope' type='b' access='read'/>"
 "<property name='AccelerometerOrientation' type='s' access='read'/>"
 "<property name='AccelerometerTilt' type='s' access='read'/>"
 "<property name='AngularVelocityX' type='d' access='read'/>"
 "<property name='AngularVelocityY' type='d' access='read'/>"
 "<property name='AngularVelocityZ' type='d' access='read'/></interface>"
 "<interface name='net.hadess.SensorProxy.Compass'>"
 "<method name='ClaimCompass'/><method name='ReleaseCompass'/>"
 "<property name='HasCompass' type='b' access='read'/>"
 "<property name='CompassHeading' type='d' access='read'/></interface></node>";
static void method(GDBusConnection *c,const gchar *sender,const gchar *path,
 const gchar *iface,const gchar *name,GVariant *params,GDBusMethodInvocation *inv,gpointer user)
{
 (void)c;(void)path;(void)iface;(void)params;(void)user;
 if(!owner)owner=g_strdup(sender);
 if(strcmp(owner,sender))wrong_owner=TRUE;
 if(g_str_has_prefix(name,"Claim"))claims++;else releases++;
 g_dbus_method_invocation_return_value(inv,g_variant_new("()"));
}
static GVariant *property(GDBusConnection *c,const gchar *sender,const gchar *path,
 const gchar *iface,const gchar *name,GError **error,gpointer user)
{
 (void)c;(void)sender;(void)path;(void)iface;(void)error;(void)user;
 if(g_str_has_prefix(name,"Has"))return g_variant_new_boolean(TRUE);
 if(!strcmp(name,"AccelerometerOrientation"))return g_variant_new_string(flat?"undefined":"normal");
 if(!strcmp(name,"AccelerometerTilt"))return g_variant_new_string(flat?"face-up":"vertical");
 if(!strcmp(name,"CompassHeading"))return g_variant_new_double(90.0);
 return g_variant_new_double(bad?NAN:.001+ticks*.00001);
}
static const GDBusInterfaceVTable vtable={.method_call=method,.get_property=property};
static gboolean emit(gpointer unused)
{
 (void)unused;ticks++;
 GVariantBuilder b;g_variant_builder_init(&b,G_VARIANT_TYPE_VARDICT);
 g_variant_builder_add(&b,"{sv}","AngularVelocityX",g_variant_new_double(bad?NAN:.001+ticks*.00001));
 g_variant_builder_add(&b,"{sv}","AccelerometerTilt",g_variant_new_string(flat?"face-up":"vertical"));
 g_dbus_connection_emit_signal(connection,NULL,"/net/hadess/SensorProxy",
  "org.freedesktop.DBus.Properties","PropertiesChanged",g_variant_new("(sa{sv}as)",
  "net.hadess.SensorProxy",&b,NULL),NULL);
 g_variant_builder_init(&b,G_VARIANT_TYPE_VARDICT);
 g_variant_builder_add(&b,"{sv}","CompassHeading",g_variant_new_double(90.0));
 g_dbus_connection_emit_signal(connection,NULL,"/net/hadess/SensorProxy/Compass",
  "org.freedesktop.DBus.Properties","PropertiesChanged",g_variant_new("(sa{sv}as)",
  "net.hadess.SensorProxy.Compass",&b,NULL),NULL);
 return G_SOURCE_CONTINUE;
}
static void finished(GObject *obj,GAsyncResult *res,gpointer unused)
{(void)unused;g_assert_true(g_subprocess_wait_finish(G_SUBPROCESS(obj),res,NULL));g_main_loop_quit(loop);}
static gboolean timeout(gpointer unused)
{(void)unused;g_subprocess_force_exit(child);g_error("Fake-bus integration timed out");return G_SOURCE_REMOVE;}
int main(int argc,char **argv)
{
 if(argc!=2)return 2;
 GTestDBus *fixture=g_test_dbus_new(G_TEST_DBUS_NONE);g_test_dbus_up(fixture);
 const char *address=g_test_dbus_get_bus_address(fixture);
 g_setenv("DBUS_SYSTEM_BUS_ADDRESS",address,TRUE);
 GError *error=NULL;
 connection=g_dbus_connection_new_for_address_sync(address,
  G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT|G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION,NULL,NULL,&error);
 g_assert_no_error(error);g_assert_nonnull(connection);
 GVariant *reply=g_dbus_connection_call_sync(connection,"org.freedesktop.DBus","/org/freedesktop/DBus",
  "org.freedesktop.DBus","RequestName",g_variant_new("(su)","net.hadess.SensorProxy",0),NULL,0,1000,NULL,&error);
 g_assert_no_error(error);g_assert_nonnull(reply);g_variant_unref(reply);
 GDBusNodeInfo *node=g_dbus_node_info_new_for_xml(xml,&error);g_assert_no_error(error);
 guint ids[2];for(guint n=0;n<2;n++) {
  ids[n]=g_dbus_connection_register_object(connection,n==1?"/net/hadess/SensorProxy/Compass":"/net/hadess/SensorProxy",node->interfaces[n],&vtable,NULL,NULL,&error);
  g_assert_no_error(error);g_assert_cmpuint(ids[n],>,0);
 }
 loop=g_main_loop_new(NULL,FALSE);guint timer=g_timeout_add(50,emit,NULL);
 for(guint test=0;test<3;test++) {
  bad=test==2;flat=test==1;claims=releases=ticks=0;wrong_owner=FALSE;g_clear_pointer(&owner,g_free);
  child=g_subprocess_new(G_SUBPROCESS_FLAGS_STDOUT_PIPE|G_SUBPROCESS_FLAGS_STDERR_PIPE,&error,argv[1],NULL);
  g_assert_no_error(error);g_subprocess_wait_async(child,NULL,finished,NULL);
  guint deadline=g_timeout_add_seconds(15,timeout,NULL);g_main_loop_run(loop);g_source_remove(deadline);
  gchar *output=NULL,*errors=NULL;g_assert_true(g_subprocess_communicate_utf8(child,NULL,NULL,&output,&errors,&error));
  g_assert_no_error(error);g_assert_false(wrong_owner);g_assert_cmpuint(claims,==,3);g_assert_cmpuint(releases,==,3);
  g_assert_cmpint(g_subprocess_get_exit_status(child),==,bad?1:0);
  g_assert_nonnull(strstr(output,bad?"complete=no":"complete=yes"));
  g_assert_nonnull(strstr(output,"claims=111 releases=111"));
  g_free(output);g_free(errors);g_object_unref(child);
 }
 g_source_remove(timer);g_main_loop_unref(loop);g_free(owner);
 for(guint n=0;n<2;n++)g_dbus_connection_unregister_object(connection,ids[n]);
 g_dbus_node_info_unref(node);g_object_unref(connection);g_test_dbus_down(fixture);g_object_unref(fixture);
 g_print("PHONE_SENSOR_BUS_REAL_GIO_FAKE_SERVICE_TESTS_PASS_NO_QRTR\n");return 0;
}
