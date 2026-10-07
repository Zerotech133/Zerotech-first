package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.param.Condition;
import com.qianjiangsoft.graphcore.param.SortingParam;
import lombok.Data;

/**
 * @author ulmarr8861
 * @date 2022/5/5
 */
@Data
public class FindSubGraphParam {
    //变类型名
    private String edgeTypeName;
    //强度
    private Condition strengthCondition;
    //次数
    private Condition countCondition;
    //排序
    private SortingParam sorting;
}
