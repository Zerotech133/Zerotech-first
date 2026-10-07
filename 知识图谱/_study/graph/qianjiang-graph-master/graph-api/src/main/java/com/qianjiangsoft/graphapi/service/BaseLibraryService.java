package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphcore.dto.BaseLibraryDTO;
import com.qianjiangsoft.graphcore.dto.BaseProcessLibraryTreeNodeDTO;
import com.qianjiangsoft.graphcore.param.BaseLibraryParam;

import java.util.List;

/**
 * 基础-分组信息Service接口
 * 
 * @author qianjiang
 * @date 2021-03-25
 */
public interface BaseLibraryService {

	List<BaseProcessLibraryTreeNodeDTO> getLibraryAndGroupTreeNodeList();

	/**
	 * 查询基础-分组信息列表
	 *
	 * @param id 主键ID
	 * @return 基础-库信息集合
	 */
	BaseLibraryDTO selectBaseLibraryById(String id);

	/**
	 * 新增基础-分组信息
	 * 
	 * @param param 基础-库信息
	 */
	void insertBaseLibrary(BaseLibraryParam param);

	/**
	 * 修改基础-分组信息
	 * 
	 * @param baseLibraryParam 基础-库信息

	 */
	void updateBaseLibrary(BaseLibraryParam baseLibraryParam);

	void dragBaseLibrary(String id, String groupId);

	/**
	 * 删除基础-分组信息信息
	 * 
	 * @param id 基础-分组信息ID
	 */
	void deleteBaseLibraryById(String id);

	/**
	 * 同一分组名称唯一性校验
	 *
	 * @param baseLibraryParam 库对象
	 * @return 结果
	 */
	void checkLibraryNameExists(BaseLibraryParam baseLibraryParam);

	/**
	 * 查询分组下的库列表
	 * @param groupId
	 * @return
	 */
	List<BaseLibraryDTO> queryLibraryListByGroupId(String groupId);
}
