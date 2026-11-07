// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2015-2019, The Linux Foundation. All rights reserved.
 * Copyright (c) 2023, Qualcomm Innovation Center, Inc. All rights reserved.
 */

#define DRIVER_NAME "msm_sharedmem"
#define pr_fmt(fmt) DRIVER_NAME ": %s: " fmt, __func__

#include <linux/uio_driver.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/err.h>
#include <linux/of.h>
#include <linux/dma-mapping.h>
#include <linux/firmware/qcom/qcom_scm.h>

#define CLIENT_ID_PROP "qcom,client-id"
#define MPSS_RMTS_CLIENT_ID 1

struct msm_sharedmem_allocation {
	void *cpu;
	phys_addr_t base;
	size_t size;
};

/* Setup the shared ram permissions.
 * This function currently supports the mpss and nav clients only.
 */
static void setup_shared_ram_perms(u32 client_id, phys_addr_t addr, u32 size,
				   bool vm_nav_path)
{
	struct qcom_scm_vmperm *dest_vmids;
	u64 source_vmlist = BIT(QCOM_SCM_VMID_HLOS);
	int ret, nr;

	if (client_id != MPSS_RMTS_CLIENT_ID)
		return;

	if (vm_nav_path) {
		nr = 3;
		dest_vmids = kcalloc(nr, sizeof(struct qcom_scm_vmperm), GFP_KERNEL);
		if (!dest_vmids) {
			pr_err("failed to alloc memory when vm_nav_path=true\n");
			return;
		}

		*dest_vmids = (struct qcom_scm_vmperm){QCOM_SCM_VMID_HLOS, QCOM_SCM_PERM_READ|QCOM_SCM_PERM_WRITE};
		*(dest_vmids + 1) = (struct qcom_scm_vmperm){QCOM_SCM_VMID_MSS_MSA, QCOM_SCM_PERM_READ|QCOM_SCM_PERM_WRITE};
		*(dest_vmids + 2) = (struct qcom_scm_vmperm){QCOM_SCM_VMID_NAV, QCOM_SCM_PERM_READ|QCOM_SCM_PERM_WRITE};
	} else {
		nr = 2;
		dest_vmids = kcalloc(nr, sizeof(struct qcom_scm_vmperm), GFP_KERNEL);
		if (!dest_vmids) {
			pr_err("failed to alloc memory when vm_nav_path=false\n");
			return;
		}
		*dest_vmids = (struct qcom_scm_vmperm){QCOM_SCM_VMID_HLOS, QCOM_SCM_PERM_READ|QCOM_SCM_PERM_WRITE};
		*(dest_vmids + 1) = (struct qcom_scm_vmperm){QCOM_SCM_VMID_MSS_MSA, QCOM_SCM_PERM_READ|QCOM_SCM_PERM_WRITE};
	}

	ret = qcom_scm_assign_mem(addr, size, &source_vmlist,
					dest_vmids, nr);
	kfree(dest_vmids);
	if (ret != 0) {
		if (ret == -EINVAL)
			pr_warn("qcom_scm_assign_mem is not supported!\n");
		else
			pr_err("qcom_scm_assign_mem failed IPA=0x016%pa size=%u err=%d\n",
				&addr, size, ret);
	}
}

static int msm_sharedmem_probe(struct platform_device *pdev)
{
	int ret = 0;
	struct uio_info *info = NULL;
	struct resource *clnt_res = NULL;
	u32 client_id = ((u32)~0U);
	u32 shared_mem_size = 0;
	u32 shared_mem_tot_sz = 0;
	void *shared_mem = NULL;
	phys_addr_t shared_mem_pyhsical = 0;
	bool guard_memory = false;
	bool vm_nav_path = false;
	phys_addr_t allocation_base = 0;
	struct msm_sharedmem_allocation *allocation;

	/* Get the addresses from platform-data */
	if (!pdev->dev.of_node) {
		pr_err("Node not found\n");
		ret = -ENODEV;
		goto out;
	}
	clnt_res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!clnt_res) {
		pr_err("resource not found\n");
		return -ENODEV;
	}

	ret = of_property_read_u32(pdev->dev.of_node, CLIENT_ID_PROP,
				   &client_id);
	if (ret) {
		client_id = ((u32)~0U);
		pr_warn("qcom,client-id property not found\n");
	}

	info = devm_kzalloc(&pdev->dev, sizeof(struct uio_info), GFP_KERNEL);
	if (!info)
		return -ENOMEM;
	allocation = devm_kzalloc(&pdev->dev, sizeof(*allocation), GFP_KERNEL);
	if (!allocation)
		return -ENOMEM;

	shared_mem_size = resource_size(clnt_res);
	shared_mem_pyhsical = clnt_res->start;

	if (shared_mem_size == 0) {
		pr_err("Shared memory size is zero\n");
		return -EINVAL;
	}

	if (shared_mem_pyhsical == 0) {

		/*
		 * If guard_memory is set, then the shared memory region
		 * will be guarded by SZ_4K at the start and at the end.
		 * This is needed to overcome the XPU limitation on few
		 * MSM HW, so as to make this memory not contiguous with
		 * other allocations that may possibly happen from other
		 * clients in the system.
		 */
		guard_memory = of_property_read_bool(pdev->dev.of_node,
				"qcom,guard-memory");

		shared_mem_tot_sz = guard_memory ? shared_mem_size + SZ_8K :
					shared_mem_size;

		shared_mem = dma_alloc_coherent(&pdev->dev, shared_mem_tot_sz,
					&shared_mem_pyhsical, GFP_KERNEL);
		if (shared_mem == NULL)
			return -ENOMEM;

		if (guard_memory)
			shared_mem_pyhsical += SZ_4K;
	}

	/*
	 * If this dtsi property is set, then the shared memory region
	 * will be given access to vm-nav-path also.
	 */
	vm_nav_path = of_property_read_bool(pdev->dev.of_node,
			"qcom,vm-nav-path");

	/* Set up the permissions for the shared ram that was allocated. */
	setup_shared_ram_perms(client_id, shared_mem_pyhsical, shared_mem_size,
				vm_nav_path);

	/* Setup device */
	info->name = clnt_res->name;
	info->version = "1.0";
	info->mem[0].addr = shared_mem_pyhsical;
	info->mem[0].size = shared_mem_size;
	info->mem[0].memtype = UIO_MEM_PHYS;
	allocation->cpu = shared_mem;
	allocation->base = allocation_base;
	allocation->size = shared_mem_tot_sz;
	info->priv = allocation;

	ret = uio_register_device(&pdev->dev, info);
	if (ret) {
		pr_err("uio register failed ret=%d\n", ret);
		goto out;
	}
	dev_set_drvdata(&pdev->dev, info);

	pr_info("Device created for client '%s'\n", clnt_res->name);
out:
	return ret;
}

static void msm_sharedmem_remove(struct platform_device *pdev)
{
	struct uio_info *info = dev_get_drvdata(&pdev->dev);

	struct msm_sharedmem_allocation *allocation = info->priv;

	uio_unregister_device(info);
	if (allocation->cpu) {
		u64 source = BIT(QCOM_SCM_VMID_HLOS);
		struct qcom_scm_vmperm destination = {
			QCOM_SCM_VMID_HLOS,
			QCOM_SCM_PERM_READ | QCOM_SCM_PERM_WRITE,
		};

		qcom_scm_assign_mem(info->mem[0].addr, info->mem[0].size,
				    &source, &destination, 1);
		dma_free_coherent(&pdev->dev, allocation->size, allocation->cpu,
				  allocation->base);
	}

	return;
}

static const struct of_device_id msm_sharedmem_of_match[] = {
	{.compatible = "qcom,sharedmem-uio",},
	{},
};
MODULE_DEVICE_TABLE(of, msm_sharedmem_of_match);

static struct platform_driver msm_sharedmem_driver = {
	.probe          = msm_sharedmem_probe,
	.remove         = msm_sharedmem_remove,
	.driver         = {
		.name   = DRIVER_NAME,
		.of_match_table = msm_sharedmem_of_match,
	},
};


static int __init msm_sharedmem_init(void)
{
	int result;

	result = platform_driver_register(&msm_sharedmem_driver);
	if (result != 0) {
		pr_err("Platform driver registration failed\n");
		return result;
	}
	return 0;
}

static void __exit msm_sharedmem_exit(void)
{
	platform_driver_unregister(&msm_sharedmem_driver);
}

module_init(msm_sharedmem_init);
module_exit(msm_sharedmem_exit);

MODULE_DESCRIPTION("MSM shared memory driver");
MODULE_LICENSE("GPL");
