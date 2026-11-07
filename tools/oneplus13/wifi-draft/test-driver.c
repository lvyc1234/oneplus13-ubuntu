/* SPDX-License-Identifier: GPL-2.0-only */
/* Execute the actual driver translation unit with synthetic transport only. */
#include "host-driver.h"
#include "driver-under-test.c"
#include "vendor-ids.h"
#include <stdio.h>

_Static_assert(PHONE_SAR_SET_COMMAND == WMI_SAR_LIMITS_CMDID, "SET id");
_Static_assert(PHONE_SAR_GET_COMMAND == WMI_SAR_GET_LIMITS_CMDID, "GET id");
_Static_assert(PHONE_SAR_RESULT_EVENT == WMI_SAR2_RESULT_EVENTID, "result id");
_Static_assert(PHONE_SAR_TABLE_EVENT == WMI_SAR_GET_LIMITS_EVENTID, "table id");

static int mode, sends, alloc_fail, recursion_status;
static struct ath12k_base *active;
static void put(unsigned char *p, u32 value)
{ p[0]=value; p[1]=value>>8; p[2]=value>>16; p[3]=value>>24; }
static void cap(struct ath12k_base *ab, u32 version)
{
	unsigned char data[8], flags[12];
	put(data, 700u<<16|4); put(data+4,version);
	ath12k_phone_sar_ready(ab,data,sizeof(data),false);
	put(flags,18u<<16|8);put(flags+4,1233u<<16|4);put(flags+8,0);
	ath12k_phone_sar_ready(ab,flags,sizeof(flags),true);
}
static void fixture(struct ath12k_base *ab, u32 version)
{
	memset(ab,0,sizeof(*ab));mode=0;sends=0;alloc_fail=0;
	active=ab;ath12k_phone_sar_init(ab);ath12k_phone_sar_attach(ab);cap(ab,version);
}
static void finish(struct ath12k_base *ab)
{
	assert(!pthread_mutex_destroy(&ab->wmi_ab.phone_sar.lock));
	assert(!pthread_mutex_destroy(&ab->wmi_ab.phone_sar.transaction.lock));
}
struct sk_buff *ath12k_wmi_alloc_skb(struct ath12k_wmi_base *wmi, size_t len)
{
	struct sk_buff *b;
	(void)wmi;
	if(alloc_fail)return NULL;
	b=malloc(sizeof(*b));assert(b);b->data=calloc(1,len);assert(b->data);return b;
}
void dev_kfree_skb(struct sk_buff *b) { free(b->data);free(b); }
int ath12k_wmi_cmd_send(struct fake_pdev *pdev, struct sk_buff *b, u32 command)
{
	unsigned char result[8],table[36]={0};
	bool v2=command==PHONE_SAR_SET_COMMAND || mode==8 || mode==9;
	u32 selection=v2?8:1;
	(void)pdev;sends++;
	assert(command==PHONE_SAR_SET_COMMAND || command==PHONE_SAR_GET_COMMAND);
	if(mode==10)return -EIO; /* sender retains skb ownership on error */
	dev_kfree_skb(b);
	if(mode==1)return 0;
	if(mode==7)ath12k_phone_sar_detach(active);
	if(mode==13)ath12k_phone_sar_attach(active);
	if(mode==15){struct psar_table tmp;recursion_status=ath12k_phone_sar_get(active,&tmp);}
	put(result,699u<<16|4);put(result+4,mode==4?1:mode==5?64:0);
	if(mode==14)selection=1;
	if(mode==18)selection=0;
	put(table,709u<<16|8);put(table+4,selection);put(table+8,selection==8?1:0);
	put(table+12,18u<<16|(selection==8?20:0));
	if(selection==8){put(table+16,710u<<16|16);put(table+32,42);}
	if(command==PHONE_SAR_SET_COMMAND && mode!=12){
		ath12k_phone_sar_event(active,PHONE_SAR_RESULT_EVENT,result,sizeof(result));
		if(mode==17)ath12k_phone_sar_event(active,PHONE_SAR_RESULT_EVENT,result,sizeof(result));
	}else if(mode==3){
		ath12k_phone_sar_event(active,PHONE_SAR_RESULT_EVENT,result,sizeof(result));
	}else{
		size_t len=selection==8?sizeof(table):16;
		if(mode==2 || mode==9)len--;
		ath12k_phone_sar_event(active,PHONE_SAR_TABLE_EVENT,table,len);
		if(mode==6)ath12k_phone_sar_event(active,PHONE_SAR_TABLE_EVENT,table,len);
	}
	return 0;
}

int main(void)
{
	struct ath12k_base ab;
	struct psar_table table,original;
	struct psar_result result;
	struct psar_row row={.value=3};
	unsigned char bytes[16]={0};
	int i,ret,groups=0;
	fixture(&ab,0);assert(!ath12k_phone_sar_get(&ab,&table));assert(table.firmware_selection==1 && sends==1);finish(&ab);groups++;
	for(i=1;i<=7;i++){
		const int failures[]={1,2,3,6,7,10,13};
		fixture(&ab,0);mode=failures[i-1];memset(&table,0xa5,sizeof(table));original=table;
		ret=ath12k_phone_sar_get(&ab,&table);assert(ret<0);assert(!memcmp(&table,&original,sizeof(table)));
		assert(ab.wmi_ab.phone_sar.poisoned);assert(ath12k_phone_sar_get(&ab,&table)==-ESHUTDOWN);assert(sends==1);
		ath12k_phone_sar_attach(&ab);cap(&ab,0);assert(ath12k_phone_sar_get(&ab,&table)==-ESHUTDOWN);finish(&ab);groups++;
	}
	fixture(&ab,1);mode=8;assert(!ath12k_phone_sar_observe_v2(&ab,&row,1,&result,&table));
	assert(sends==2 && result.raw_result==0 && table.firmware_selection==8 && table.rows[0].value==42);
	/* Input3 is an index; observed42 is half-dBm. Never compare as one unit. */
	finish(&ab);groups++;
	for(i=0;i<9;i++){
		const int failures[]={1,4,5,9,10,12,13,14,17};
		fixture(&ab,1);mode=failures[i];memset(&result,0xa5,sizeof(result));
		memset(&table,0xa5,sizeof(table));original=table;
		assert(ath12k_phone_sar_observe_v2(&ab,&row,1,&result,&table)<0);
		assert(!memcmp(&table,&original,sizeof(table)));
		if(mode==4)assert(result.raw_result==1 && !result.firmware_reported_success);
		if(mode==5)assert(result.raw_result==64 && !result.recognized);
		assert(ab.wmi_ab.phone_sar.poisoned);finish(&ab);groups++;
	}
	fixture(&ab,0);assert(ath12k_phone_sar_observe_v2(&ab,&row,1,&result,&table)==-EOPNOTSUPP && !sends);finish(&ab);groups++;
	fixture(&ab,99);assert(ath12k_phone_sar_get(&ab,&table)==-EOPNOTSUPP && !sends);assert(ab.wmi_ab.phone_sar.raw_version==99);finish(&ab);groups++;
	fixture(&ab,1);ab.wmi_ab.phone_sar.raw_flags=1;assert(ath12k_phone_sar_observe_v2(&ab,&row,1,&result,&table)==-EOPNOTSUPP && !sends);finish(&ab);groups++;
	fixture(&ab,1);ab.wmi_ab.phone_sar.flags_seen=false;assert(ath12k_phone_sar_observe_v2(&ab,&row,1,&result,&table)==-EOPNOTSUPP && !sends);finish(&ab);groups++;
	fixture(&ab,0);alloc_fail=1;assert(ath12k_phone_sar_get(&ab,&table)==-ENOMEM && !sends);alloc_fail=0;assert(!ath12k_phone_sar_get(&ab,&table));finish(&ab);groups++;
	fixture(&ab,0);mode=15;assert(!ath12k_phone_sar_get(&ab,&table));assert(recursion_status==-EBUSY && sends==1);finish(&ab);groups++;
	fixture(&ab,0);cap(&ab,0);assert(ab.wmi_ab.phone_sar.poisoned && !sends);finish(&ab);groups++;
	for(i=0;i<12;i++){
		fixture(&ab,0);put(bytes,18u<<16|8);put(bytes+4,1233u<<16|4);
		ath12k_phone_sar_ready(&ab,bytes,i,true);
		if(i)assert(ab.wmi_ab.phone_sar.poisoned);
		finish(&ab);
	}groups++;
	fixture(&ab,0);assert(!ath12k_phone_sar_event(&ab,0,bytes,sizeof(bytes)));assert(!ab.wmi_ab.phone_sar.poisoned);finish(&ab);groups++;
	fixture(&ab,1);row.value=12;assert(ath12k_phone_sar_observe_v2(&ab,&row,1,&result,&table)==-EINVAL && !sends);finish(&ab);groups++;
	fixture(&ab,5);mode=18;assert(!ath12k_phone_sar_get(&ab,&table));assert(table.firmware_selection==0 && !table.count);
	row.value=3;assert(ath12k_phone_sar_observe_v2(&ab,&row,1,&result,&table)==-EOPNOTSUPP && sends==1);finish(&ab);groups++;
	printf("driver_groups=%d actual_driver_code=yes tokenless_timeout_latched=yes hardware=no\n",groups);
	return 0;
}
