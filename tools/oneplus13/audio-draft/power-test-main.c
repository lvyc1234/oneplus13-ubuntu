/* SPDX-License-Identifier: GPL-2.0-only */
static struct aw88461 init_amp(struct regmap *map,struct gpio_desc *gpio,unsigned int volume)
{
	memset(map,0,sizeof(*map)); memset(gpio,0,sizeof(*gpio));
	map->regs[1]=AW88461_PLL_LOCK|AW88461_BOOST_READY|AW88461_SWITCHING;
	map->regs[4]=AW88461_PWDN|AW88461_AMPPD|AW88461_HMUTE|AW88461_ULS_HMUTE;
	calls=0; fail_at=0; stop_at_read=0; unsafe_unmute=false;
	return (struct aw88461){.aw_dev={.regmap=map},.reset=gpio,.volume=volume,
		.profile_volume=256,.profile_txen=AW88461_TXEN,.enabled=true,.running=true};
}
int main(void)
{
	struct regmap map;
	struct gpio_desc gpio;
	struct aw88461 amp=init_amp(&map,&gpio,400);
	unsigned int start_calls,stop_calls,i,failed=0;
	current=&amp;
	CHECK(!aw88461_start(&amp)); CHECK(amp.powered && !amp.faulted && !unsafe_unmute);
	CHECK((map.regs[5]&1023)==879); start_calls=calls;
	calls=0; CHECK(!aw88461_stop(&amp)); stop_calls=calls;
	CHECK(!amp.powered && !amp.faulted && (map.regs[4]&3)==3);
	CHECK((map.regs[5]&1023)==1023);
	for(i=1;i<=start_calls;i++) {
		amp=init_amp(&map,&gpio,400); current=&amp; fail_at=i;
		CHECK(aw88461_start(&amp)==-EIO); CHECK(amp.faulted && !amp.powered && gpio.asserted);
		CHECK(calls==i && !unsafe_unmute); CHECK(aw88461_start(&amp)==-EIO && calls==i); failed++;
	}
	for(i=1;i<=stop_calls;i++) {
		amp=init_amp(&map,&gpio,400); current=&amp; CHECK(!aw88461_start(&amp));
		calls=0; fail_at=i; CHECK(aw88461_stop(&amp)==-EIO);
		CHECK(amp.faulted && !amp.powered && gpio.asserted && calls==i); failed++;
	}
	amp=init_amp(&map,&gpio,0); current=&amp;
	CHECK(!aw88461_start(&amp) && (map.regs[5]&1023)==1023);
	amp=init_amp(&map,&gpio,400); current=&amp; map.regs[1]=0;
	CHECK(aw88461_start(&amp)==-ETIMEDOUT && amp.faulted && gpio.asserted);
	amp=init_amp(&map,&gpio,400); current=&amp; map.regs[1]|=BIT(14);
	CHECK(aw88461_start(&amp)==-EIO && amp.faulted && gpio.asserted);
	amp=init_amp(&map,&gpio,400); current=&amp; stop_at_read=2;
	CHECK(!aw88461_start(&amp) && !amp.powered && !amp.faulted && !unsafe_unmute);
	amp=init_amp(&map,&gpio,400); current=&amp; amp.enabled=false;
	CHECK(!aw88461_start(&amp) && calls==0);
	printf("AW88461 actual power callbacks: %u I/O failures, clock timeout/fault/cancel/default attenuation checked.\n",failed);
	return 0;
}
