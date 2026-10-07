package com.qianjiangsoft.graphcore.page;

import lombok.Data;

/**
 * 分页数据
 * 
 * @author qianjiang
 */
@Data
public class PageParam {
	/** 当前记录起始索引 */
	private Integer pageNum;

	/** 每页显示记录数 */
	private Integer pageSize;

	/** 排序列 */
	private String orderBy;
}
