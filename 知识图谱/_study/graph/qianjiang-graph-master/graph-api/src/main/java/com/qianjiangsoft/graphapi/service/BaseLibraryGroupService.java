package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphcore.dto.BaseLibraryGroupDTO;
import com.qianjiangsoft.graphcore.param.BaseLibraryGroupParam;

import java.util.List;

/**
 * 基础-分组信息Service接口
 * 
 * @author qianjiang
 * @date 2021-03-25
 */
public interface BaseLibraryGroupService {

	/**
	 * 查询基础-分组信息列表
	 * 
	 * @return 基础-分组信息集合
	 */
	List<BaseLibraryGroupDTO> selectBaseLibraryGroupList();

	/**
	 * 查询基础-分组信息列表
	 *
	 * @param id 主键ID
	 * @return 基础-分组信息集合
	 */
	BaseLibraryGroupDTO selectBaseLibraryGroupById(String id);

	/**
	 * 新增基础-分组信息
	 * 
	 * @param baseLibraryGroupParam 基础-分组信息
	 * @return 结果
	 */
	void insertBaseTableGroup(BaseLibraryGroupParam baseLibraryGroupParam);

	/**
	 * 修改基础-分组信息
	 * 
	 * @param baseTableGroupParam 基础-分组信息
	 * @return 结果
	 */
	void updateBaseTableGroup(BaseLibraryGroupParam baseTableGroupParam);

	/**
	 * 删除基础-分组信息信息
	 * 
	 * @param id 基础-分组信息ID
	 */
	void deleteBaseLibraryGroupById(String id);

	/**
	 * 同一分组名称唯一性校验
	 *
	 * @param baseLibraryGroupParam 分组对象
	 * @return 结果
	 */
	void checkGroupNameExists(BaseLibraryGroupParam baseLibraryGroupParam);


	void dragBaseLibraryGroup(String id, String parentId);
}
