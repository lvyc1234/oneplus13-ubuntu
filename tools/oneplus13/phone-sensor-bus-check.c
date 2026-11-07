/* SPDX-License-Identifier: GPL-3.0-or-later */
/* One bounded client, claims and releases held on the SAME D-Bus connection. */
#include <gio/gio.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static const char *name="net.hadess.SensorProxy", *path="/net/hadess/SensorProxy";
static GDBusConnection *bus;
static guint updates,accel_updates,compass_updates;
static GVariant *call(const char *object,const char *iface,const char *method,GVariant *args)
{
 GError *error=NULL;
 GVariant *reply=g_dbus_connection_call_sync(bus,name,object,iface,method,args,NULL,
   G_DBUS_CALL_FLAGS_NONE,1800,NULL,&error);
 if(error) {g_printerr("SENSOR_BUS_CALL_FAILED method=%s code=%d\n",method,error->code);g_error_free(error);}
 return reply;
}
static gboolean method(const char *iface,const char *verb,const char *kind)
{
 const char *object=!strcmp(iface,"net.hadess.SensorProxy.Compass")?"/net/hadess/SensorProxy/Compass":path;
 gchar *method_name=g_strconcat(verb,kind,NULL);GVariant *reply=call(object,iface,method_name,NULL);
 g_free(method_name);if(!reply)return FALSE;g_variant_unref(reply);return TRUE;
}
static GVariant *props(const char *iface)
{
 const char *object=!strcmp(iface,"net.hadess.SensorProxy.Compass")?"/net/hadess/SensorProxy/Compass":path;
 GVariant *reply=call(object,"org.freedesktop.DBus.Properties","GetAll",g_variant_new("(s)",iface));
 if(!reply)return NULL;
 GVariant *dict=NULL;g_variant_get(reply,"(@a{sv})",&dict);g_variant_unref(reply);return dict;
}
static gboolean flag(GVariant *dict,const char *key)
{gboolean value=FALSE;return dict && g_variant_lookup(dict,key,"b",&value) && value;}
static gboolean number(GVariant *dict,const char *key,double *value)
{return dict && g_variant_lookup(dict,key,"d",value) && isfinite(*value);}
static gboolean posture(GVariant *dict)
{
 const char *o=NULL,*tilt=NULL;
 const char *orientations[]={"undefined","normal","bottom-up","left-up","right-up",NULL};
 const char *tilts[]={"undefined","vertical","tilted-up","tilted-down","face-up","face-down",NULL};
 if(!dict || !g_variant_lookup(dict,"AccelerometerOrientation","&s",&o) ||
    !g_variant_lookup(dict,"AccelerometerTilt","&s",&tilt))return FALSE;
 gboolean known_o=FALSE,known_t=FALSE;
 for(guint n=0;orientations[n];n++)known_o|=!strcmp(o,orientations[n]);
 for(guint n=0;tilts[n];n++)known_t|=!strcmp(tilt,tilts[n]);
 /* A stationary face-up phone cannot identify its in-plane orientation. */
 return known_o && known_t && (strcmp(o,"undefined") || strcmp(tilt,"undefined"));
}
static gboolean vectors(GVariant *dict,double *norm)
{
 double x,y,z;
 if(!number(dict,"AngularVelocityX",&x) || !number(dict,"AngularVelocityY",&y) || !number(dict,"AngularVelocityZ",&z))return FALSE;
 *norm=sqrt(x*x+y*y+z*z);return isfinite(*norm) && *norm<1000;
}
static void changed(GDBusConnection *c,const gchar *sender,const gchar *obj,
 const gchar *iface,const gchar *signal,GVariant *parameters,gpointer user)
{
 (void)c;(void)sender;(void)obj;(void)iface;(void)signal;(void)user;
 const char *changed_iface;GVariant *dict,*invalid;
 g_variant_get(parameters,"(&s@a{sv}@as)",&changed_iface,&dict,&invalid);
 if(!strcmp(changed_iface,name)) {
  GVariant *value=g_variant_lookup_value(dict,"AngularVelocityX",G_VARIANT_TYPE_DOUBLE);
  if(value) {if(isfinite(g_variant_get_double(value)))updates++;g_variant_unref(value);}
  value=g_variant_lookup_value(dict,"AccelerometerTilt",G_VARIANT_TYPE_STRING);
  if(!value)value=g_variant_lookup_value(dict,"AccelerometerOrientation",G_VARIANT_TYPE_STRING);
  if(value) {accel_updates++;g_variant_unref(value);}
 } else if(!strcmp(changed_iface,"net.hadess.SensorProxy.Compass")) {
  GVariant *value=g_variant_lookup_value(dict,"CompassHeading",G_VARIANT_TYPE_DOUBLE);
  if(value) {if(isfinite(g_variant_get_double(value)))compass_updates++;g_variant_unref(value);}
 }
 g_variant_unref(dict);g_variant_unref(invalid);
}
static void self_test(void)
{
 GVariantBuilder b;g_variant_builder_init(&b,G_VARIANT_TYPE_VARDICT);
 g_variant_builder_add(&b,"{sv}","AngularVelocityX",g_variant_new_double(1));
 g_variant_builder_add(&b,"{sv}","AngularVelocityY",g_variant_new_double(2));
 g_variant_builder_add(&b,"{sv}","AngularVelocityZ",g_variant_new_double(2));
 GVariant *d=g_variant_ref_sink(g_variant_builder_end(&b));double norm=0;
 g_assert_true(vectors(d,&norm));g_assert_cmpfloat(norm,==,3);g_variant_unref(d);
 for(guint n=0;n<3;n++) {
  g_variant_builder_init(&b,G_VARIANT_TYPE_VARDICT);
  g_variant_builder_add(&b,"{sv}","AngularVelocityX",g_variant_new_double(n==0?NAN:INFINITY));
  g_variant_builder_add(&b,"{sv}","AngularVelocityY",g_variant_new_double(0));
  if(n!=2)g_variant_builder_add(&b,"{sv}","AngularVelocityZ",g_variant_new_double(0));
  d=g_variant_ref_sink(g_variant_builder_end(&b));g_assert_false(vectors(d,&norm));g_variant_unref(d);
 }
 puts("PHONE_SENSOR_BUS_VARIANT_CHECKS_PASS_NO_BUS_NO_QRTR");
}
int main(int argc,char **argv)
{
 if(argc==2 && !strcmp(argv[1],"--self-test")) {self_test();return 0;}
 if(argc!=1)return 2;
 GError *error=NULL;bus=g_bus_get_sync(G_BUS_TYPE_SYSTEM,NULL,&error);
 if(!bus) {g_clear_error(&error);return 3;}
 const char *compass="net.hadess.SensorProxy.Compass";
 gboolean claimed[3]={FALSE,FALSE,FALSE},released[3]={FALSE,FALSE,FALSE},okay=FALSE;
 guint token=g_dbus_connection_signal_subscribe(bus,name,"org.freedesktop.DBus.Properties",
  "PropertiesChanged",path,NULL,G_DBUS_SIGNAL_FLAGS_NONE,changed,NULL,NULL);
 guint compass_token=g_dbus_connection_signal_subscribe(bus,name,"org.freedesktop.DBus.Properties",
  "PropertiesChanged","/net/hadess/SensorProxy/Compass",NULL,G_DBUS_SIGNAL_FLAGS_NONE,changed,NULL,NULL);
 gint64 until=g_get_monotonic_time()+8*G_USEC_PER_SEC;
 while(g_get_monotonic_time()<until) {
  GVariant *d=props(name),*m=props(compass);
  gboolean ready=flag(d,"HasAccelerometer") && flag(d,"HasGyroscope") && flag(m,"HasCompass");
  if(d)g_variant_unref(d);
  if(m)g_variant_unref(m);
  if(ready) {okay=TRUE;break;}g_usleep(100000);
 }
 if(!okay)goto out;
 claimed[0]=method(name,"Claim","Accelerometer");
 claimed[1]=method(name,"Claim","Gyroscope");
 claimed[2]=method(compass,"Claim","Compass");
 okay=claimed[0] && claimed[1] && claimed[2];
 for(guint n=0;n<8 && okay;n++) {
  /* Dispatch signals while keeping the same claim-owning connection alive. */
  gint64 end=g_get_monotonic_time()+200000;
  while(g_get_monotonic_time()<end) {while(g_main_context_iteration(NULL,FALSE)) {} g_usleep(10000);}
  GVariant *d=props(name),*m=props(compass);double norm=0,heading=-1;
  okay=posture(d) && vectors(d,&norm) &&
        number(m,"CompassHeading",&heading) && heading>=0 && heading<=360;
  if(d)g_variant_unref(d);
  if(m)g_variant_unref(m);
 }
 okay=okay && updates>=3 && accel_updates>=1 && compass_updates>=1;
out:
 for(gint n=2;n>=0;n--)if(claimed[n])released[n]=method(n==2?compass:name,"Release",
  n==0?"Accelerometer":n==1?"Gyroscope":"Compass");
 for(guint n=0;n<3;n++)okay=okay && claimed[n] && released[n];
 g_dbus_connection_signal_unsubscribe(bus,token);g_dbus_connection_signal_unsubscribe(bus,compass_token);g_object_unref(bus);
 printf("PHONE_SENSOR_BUS_RESULT complete=%s gyro_updates=%u accel_updates=%u compass_updates=%u claims=%u%u%u releases=%u%u%u physical_axes_accepted=no\n",
  okay?"yes":"no",updates,accel_updates,compass_updates,claimed[0],claimed[1],claimed[2],released[0],released[1],released[2]);
 return okay?0:1;
}
