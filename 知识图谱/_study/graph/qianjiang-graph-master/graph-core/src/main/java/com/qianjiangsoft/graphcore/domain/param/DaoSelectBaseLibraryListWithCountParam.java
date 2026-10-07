package com.qianjiangsoft.graphcore.domain.param;

import lombok.Data;

/**
 * 基础-分组信息对象 base_table_group
 * 
 * @author qianjiang
 * @date 2021-03-25
 */
@Data
public class DaoSelectBaseLibraryListWithCountParam {
	private static final long serialVersionUID = 1L;

	private String name;

	private String groupId;

	private String spaceId;
}
