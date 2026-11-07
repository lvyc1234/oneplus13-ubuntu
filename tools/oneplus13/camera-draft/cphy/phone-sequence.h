/* SPDX-License-Identifier: GPL-2.0-only */
/* Isolated phone C-PHY sequence planner. No MMIO, device or kernel binding.
 * Register data and ordering originate in the pinned OnePlusOSS GPL driver.
 * Power, clocks, reset assertion, CPAS and ownership must precede this sequence;
 * this planner does not implement those prerequisites or qualify hardware.
 */
#ifndef ONEPLUS13_PHONE_CPHY_SEQUENCE_H
#define ONEPLUS13_PHONE_CPHY_SEQUENCE_H
#define PHONE_CPHY_MAX_OPS 128U
enum phone_cphy_operation { PHONE_POLL, PHONE_UPDATE, PHONE_WRITE };
struct phone_cphy_op {
    unsigned int type, address, value, mask, delay_us, timeout_us;
};
struct phone_cphy_plan {
    unsigned int count, settle_count, rate_index;
    struct phone_cphy_op ops[PHONE_CPHY_MAX_OPS];
};

static int phone_cphy_emit(struct phone_cphy_plan *plan, unsigned int type,
                          unsigned int address, unsigned int value,
                          unsigned int mask, unsigned int delay, unsigned int timeout)
{
    if (plan->count >= PHONE_CPHY_MAX_OPS || address % 4 || address > 0x1184 ||
        value > 255 || mask > 255 || delay > 10000 || timeout > 10000)
        return -1;
    plan->ops[plan->count++] = (struct phone_cphy_op){type,address,value,mask,delay,timeout};
    return 0;
}

static int phone_cphy_append(struct phone_cphy_plan *plan,
                             const struct phone_cphy_reg *rows, unsigned int count)
{
    for (unsigned int i=0;i<count;i++) {
        unsigned int value=rows[i].value;
        switch (rows[i].type) {
        case 0: case 1: break; /* default or default CDR, no tuning override */
        case 2: value=plan->settle_count & 255U; break;
        case 3: value=(plan->settle_count >> 8) & 255U; break;
        default: return -1;
        }
        if (phone_cphy_emit(plan,PHONE_WRITE,rows[i].address,value,0,
                            rows[i].delay_us,0)) return -1;
    }
    return 0;
}

static int phone_cphy_build_plan(struct phone_cphy_plan *plan,
                                 unsigned long long symbol_rate,
                                 unsigned int timer_hz, unsigned int prepare_ns,
                                 unsigned int preamble_ns, unsigned int trio_mask)
{
    if (!plan) return -1;
    *plan=(struct phone_cphy_plan){0};
    int rate=phone_cphy_rate_index(symbol_rate);
    /* This phone candidate supports exactly the observed three-trio family.
     * No guessed timings, partial/combo PHY mode or alternate timer accepted.
     */
    if (rate<0 || timer_hz!=400000000U || !prepare_ns || !preamble_ns ||
        prepare_ns>1000000U || preamble_ns>1000000U || trio_mask!=7U) return -1;
    unsigned long long settle=((unsigned long long)prepare_ns+preamble_ns/3U)*timer_hz;
    settle/=1000000000ULL;
    if (settle<=10 || settle-10>65535U) return -1;
    plan->settle_count=(unsigned int)(settle-10);
    plan->rate_index=(unsigned int)rate;
    if (phone_cphy_emit(plan,PHONE_POLL,0x1184,0x80,0x80,500,10000) ||
        phone_cphy_emit(plan,PHONE_UPDATE,0x1014,0x2a,0x2a,0,0) ||
        phone_cphy_append(plan,phone_common,4) ||
        phone_cphy_append(plan,rates[rate].registers,rates[rate].count) ||
        phone_cphy_append(plan,phone_base,72) ||
        phone_cphy_append(plan,phone_irq,11) ||
        phone_cphy_emit(plan,PHONE_WRITE,0x1000,0x0e,0,3048,0)) {
        *plan=(struct phone_cphy_plan){0}; return -1;
    }
    return 0;
}

/* Callback executor remains useful for fault-injection tests. The component
 * builder supplies only a fake register array; no production MMIO adapter.
 * Poll/write failures abort immediately, with no later reset-release operation.
 */
static int phone_cphy_execute(const struct phone_cphy_plan *plan,
                              int (*operation)(void *,const struct phone_cphy_op *),
                              void *context)
{
    if (!plan || !operation || plan->count!=126 || plan->ops[0].type!=PHONE_POLL)
        return -1;
    for (unsigned int i=0;i<plan->count;i++) {
        int result=operation(context,&plan->ops[i]);
        if (result) return result;
    }
    return 0;
}
#endif
