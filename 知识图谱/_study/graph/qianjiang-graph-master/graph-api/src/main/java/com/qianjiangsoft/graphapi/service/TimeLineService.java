package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.TimeLineEventDTO;
import com.qianjiangsoft.graphapi.param.QueryEventListParam;
import com.qianjiangsoft.graphapi.param.TimeLineEventParam;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/4/24 17:53
 */
public interface TimeLineService {

    /**
     * 批量导入事件
     * @param: paramList
     * @return: void
     * @author: marshall
     * @date: 2022/4/25
    */
    void importEventData(List<TimeLineEventParam> paramList);

    /**
     * 查询时光轴数据
     * @param: param
     * @return: java.util.List<com.qianjiangsoft.graphapi.dto.TimeLineEventDTO>
     * @author: marshall
     * @date: 2022/4/25
    */
    List<TimeLineEventDTO> queryEventList(QueryEventListParam param);

    void clearCaseEventData(String caseId);
}
