package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphcore.dto.BaseRelationalGraphDataDTO;
import com.qianjiangsoft.graphcore.dto.BaseRelationalGraphDetailDTO;
import com.qianjiangsoft.graphcore.page.PageDataVO;
import com.qianjiangsoft.graphcore.param.AddBaseRelationalGraphParam;
import com.qianjiangsoft.graphcore.param.UpdateBaseRelationalGraphDataParam;
import com.qianjiangsoft.graphcore.param.UpdateBaseRelationalGraphParam;

/**
 * 基础-关系图谱服务接口
 * 
 * @author qianjiang
 * @date 2021-04-25
 */
public interface BaseRelationalGraphService {

	/**
	 * 查询基础-查询库下所有图谱信息
	 *
	 * @param libraryId 基础-分组关联模型ID
	 * @return 基础-分组关联模型
	 */
	PageDataVO selectGraphPageByLibraryId(String libraryId, String keyword);

	/**
	 * 
	 * @author marshallwu
	 * 根据id查询关系图谱信息
	 * @return:
	 */
	BaseRelationalGraphDetailDTO getGraphDetailById(String id);

	String addGraph(AddBaseRelationalGraphParam param);

	void updateGraph(UpdateBaseRelationalGraphParam param);

	void updateGraphData(UpdateBaseRelationalGraphDataParam param);

	void deleteGraphById(String id);

	BaseRelationalGraphDataDTO getGraphDataById(String id);
}
