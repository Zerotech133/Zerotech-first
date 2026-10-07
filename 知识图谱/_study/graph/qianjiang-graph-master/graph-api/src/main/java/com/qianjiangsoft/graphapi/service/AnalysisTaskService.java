package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.AddGraphAnalyseTaskParam;
import com.qianjiangsoft.graphapi.dto.QueryGraphAnalyseTaskListDTO;
import com.qianjiangsoft.graphapi.param.analysisTask.SearchTaskParam;
import com.qianjiangsoft.graphcore.page.PageDataVO;

public interface AnalysisTaskService {

    PageDataVO<QueryGraphAnalyseTaskListDTO> queryListByParam(SearchTaskParam param);

    Integer addAnalysisTask(AddGraphAnalyseTaskParam param);

    void deleteTask(Integer id);

    Object queryTaskResult(Integer id);

    void updateAnalysisTaskStatus(Integer id, Integer status);
}
