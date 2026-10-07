package com.qianjiangsoft.graphcore.domain.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import lombok.Data;
import lombok.ToString;
import lombok.experimental.Accessors;

/**
 * 基础-分组信息对象 base_table_group
 * 
 * @author qianjiang
 * @date 2021-03-25
 */
@Data
@ToString
@Accessors(chain = true)
public class BaseLibraryGroup {
	private static final long serialVersionUID = 1L;

	@TableId
	private String id;

	private String name;

	private String parentId;

	private String spaceId;
}
