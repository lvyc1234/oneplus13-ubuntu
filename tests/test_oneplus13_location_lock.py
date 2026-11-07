# SPDX-License-Identifier: GPL-2.0-only
"""Validate actual read-only LOC lock decoding with independent wire fixtures."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#define main unused_location_session_main
#include "location-session.c"
#undef main
#include <string.h>
static QmiMessage *wire(guint8 flags, guint8 cid, guint16 id, const guint8 *p, guint n)
{
 guint8 head[]={1,0,0,0x80,QMI_SERVICE_LOC,cid,flags,1,0,id&255,id>>8,n&255,n>>8};
 GByteArray *bytes=g_byte_array_new(); GError *error=NULL; QmiMessage *msg;
 guint length=sizeof(head)+n-1;head[1]=length&255;head[2]=length>>8;
 g_byte_array_append(bytes,head,sizeof(head));g_byte_array_append(bytes,p,n);
 msg=qmi_message_new_from_raw(bytes,&error);g_assert_no_error(error);g_assert_nonnull(msg);
 g_byte_array_unref(bytes);return msg;
}
static const guint8 full[]={
 1,4,0,0,0,0,0,
 0x10,4,0,4,0,0,0,
 0x11,4,0,2,0,0,0,
 0x12,8,0,7,0,0,0,0,0,0,0,
 0x13,8,0,0,0,0,0,1,0,0,0,
 0x14,4,0,2,0,0,0};
static void rejected(QmiMessage *m) { g_assert_null(lock_indication_report(m,0x27));qmi_message_unref(m); }
int main(int argc,char **argv)
{
 QmiMessage *m;gchar *s;guint8 p[sizeof(full)+16];g_assert_cmpint(argc,==,2);
 if(!strcmp(argv[1],"full")) {
   m=wire(4,0x27,0x3b,full,sizeof(full));s=lock_indication_report(m,0x27);
   g_assert_cmpstr(s,==,"{\"read_only\":true,\"default_data_subscription\":true,\"status\":0,\"lock_type\":4,\"subscription\":2,\"lock_client\":\"0x0000000000000007\",\"clients_config\":\"0x0000000100000000\",\"engine_lock_state\":2}");g_free(s);qmi_message_unref(m);
 } else if(!strcmp(argv[1],"missing")) {
   m=wire(4,0x27,0x3b,full,7);s=lock_indication_report(m,0x27);
   g_assert_nonnull(strstr(s,"\"lock_type\":null"));g_assert_nonnull(strstr(s,"\"engine_lock_state\":null"));g_free(s);qmi_message_unref(m);
   rejected(wire(4,0x27,0x3b,full+7,sizeof(full)-7));
 } else if(!strcmp(argv[1],"malformed")) {
   const guint8 ids[]={1,0x10,0x11,0x12,0x13,0x14};
   for(guint i=0;i<sizeof(ids);i++) {
     guint expected=(i==3||i==4)?8:4;
     for(guint n=0;n<=9;n++) if(n!=expected) {
       memcpy(p,full,7);p[7]=ids[i];p[8]=n;p[9]=0;memset(p+10,0,n);
       rejected(wire(4,0x27,0x3b,p,10+n));
     }
   }
 } else if(!strcmp(argv[1],"duplicate")) {
   guint off=0;while(off<sizeof(full)) {
     guint n=full[off+1]+3;memcpy(p,full,sizeof(full));memcpy(p+sizeof(full),full+off,n);
     rejected(wire(4,0x27,0x3b,p,sizeof(full)+n));off+=n;
   }
 } else if(!strcmp(argv[1],"identity")) {
   rejected(wire(2,0x27,0x3b,full,sizeof(full)));rejected(wire(0,0x27,0x3b,full,sizeof(full)));
   rejected(wire(4,0xff,0x3b,full,sizeof(full)));rejected(wire(4,0x27,0x3a,full,sizeof(full)));
   m=wire(4,0x27,0x3b,full,sizeof(full));m->data[4]=QMI_SERVICE_DMS;rejected(m);
 } else if(!strcmp(argv[1],"status")) {
   for(guint n=1;n<=11;n++){memcpy(p,full,sizeof(full));p[3]=n;rejected(wire(4,0x27,0x3b,p,sizeof(full)));}
 } else if(!strcmp(argv[1],"response")) {
   guint8 ok[]={2,4,0,0,0,0,0};
   m=wire(2,0x27,0x3b,ok,7);g_assert_true(lock_response_valid(m,0x27,1));
   g_assert_false(lock_response_valid(m,0x26,1));g_assert_false(lock_response_valid(m,0x27,2));qmi_message_unref(m);
   memcpy(p,ok,7);memcpy(p+7,ok,7);m=wire(2,0x27,0x3b,p,14);g_assert_false(lock_response_valid(m,0x27,1));qmi_message_unref(m);
   ok[3]=1;ok[5]=46;m=wire(2,0x27,0x3b,ok,7);g_assert_false(lock_response_valid(m,0x27,1));qmi_message_unref(m);
   ok[3]=0;m=wire(2,0x27,0x3b,ok,7);g_assert_false(lock_response_valid(m,0x27,1));qmi_message_unref(m);
 } else if(!strcmp(argv[1],"both")) {
   struct session session={0};session.lock_report=g_strdup("{}");exit_status=1;
   session.lock_response_ok=TRUE;lock_maybe_finish(&session);g_assert_cmpint(exit_status,==,1);
   session.lock_response_ok=FALSE;session.lock_indication_ok=TRUE;lock_maybe_finish(&session);g_assert_cmpint(exit_status,==,1);
   session.lock_response_ok=TRUE;lock_maybe_finish(&session);g_assert_cmpint(exit_status,==,0);g_free(session.lock_report);
 } else g_assert_not_reached();
 return 0;
}
'''


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc')
                     and shutil.which('pkg-config'), 'Requires Linux GCC and pkg-config')
class LocationLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        packages = subprocess.run(['pkg-config','--cflags','--libs','qmi-glib','qrtr-glib','gio-2.0'],
                                  capture_output=True,text=True,check=False,timeout=10)
        if packages.returncode:
            raise unittest.SkipTest('libqmi/QRTR development packages unavailable')
        flags = packages.stdout
        cls.temp = tempfile.TemporaryDirectory(prefix='op13-lock-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name)/'lock.c';source.write_text(HARNESS)
        cls.binary = source.with_suffix('')
        subprocess.run(['gcc','-std=c11','-O1','-Wall','-Wextra','-Werror',
                        '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
                        '-I',str(ROOT/'tools/oneplus13'),str(source),'-o',str(cls.binary),
                        *shlex.split(flags)],check=True,timeout=30)

    def check(self, name):
        p = subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=5,
                           env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1:halt_on_error=1'})
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)

    def test_all_primary_schema_fields_and_u64_endianness(self): self.check('full')
    def test_missing_optional_fields_are_unknown_and_status_required(self): self.check('missing')
    def test_each_known_field_rejects_wrong_width(self): self.check('malformed')
    def test_each_duplicate_known_field_rejected(self): self.check('duplicate')
    def test_foreign_client_service_message_and_non_indication_rejected(self): self.check('identity')
    def test_unsuccessful_indication_status_rejected(self): self.check('status')
    def test_response_identity_result_and_duplicates_checked(self): self.check('response')
    def test_both_response_and_indication_required(self): self.check('both')


if __name__ == '__main__': unittest.main()
