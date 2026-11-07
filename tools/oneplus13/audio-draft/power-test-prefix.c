/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
#ifndef ETIMEDOUT
#define ETIMEDOUT 110
#endif
#define BIT(x) (1U << (x))
#define GENMASK(h,l) ((0xffffffffU >> (31-(h))) & (0xffffffffU << (l)))
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define READ_ONCE(x) (x)
#define dev_err(...) ((void)0)
#define usleep_range(a,b) ((void)(a),(void)(b))
#define CHECK(c) do { if (!(c)) { fprintf(stderr,"Failure line %d: %s\n",__LINE__,#c); return 1; } } while (0)
struct regmap { unsigned int regs[128]; };
struct aw_device { struct regmap *regmap; void *dev; };
struct gpio_desc { unsigned int asserted; };
struct aw88461 {
	struct aw_device aw_dev;
	struct gpio_desc *reset;
	unsigned int volume,profile_volume,profile_txen;
	bool enabled,running,powered,faulted;
};
static unsigned int calls,fail_at,stop_at_read;
static bool unsafe_unmute;
static struct aw88461 *current;
static int regmap_update_bits(struct regmap *map,unsigned int reg,unsigned int mask,unsigned int value)
{
	if (++calls==fail_at) return -EIO;
	/* Unmuting before clock/boost readiness or with a powered-down amp fails. */
	if (reg==4 && (mask & BIT(8)) && !(value & BIT(8)) &&
	    (((map->regs[1] & (BIT(0)|BIT(4)|BIT(9)|BIT(8))) != (BIT(0)|BIT(4)|BIT(9)|BIT(8))) ||
	     (map->regs[4] & 3))) unsafe_unmute=true;
	map->regs[reg]=(map->regs[reg]&~mask)|(value&mask);
	return 0;
}
static int regmap_read(struct regmap *map,unsigned int reg,unsigned int *value)
{
	if (++calls==fail_at) return -EIO;
	if (calls==stop_at_read) current->running=false;
	*value=map->regs[reg];
	return 0;
}
#define regmap_read_poll_timeout(map,reg,value,condition,delay,timeout) \
	({ int result=regmap_read(map,reg,&value); if (!result && !(condition)) result=-ETIMEDOUT; result; })
static void gpiod_set_value_cansleep(struct gpio_desc *gpio,int value) { gpio->asserted=value; }
