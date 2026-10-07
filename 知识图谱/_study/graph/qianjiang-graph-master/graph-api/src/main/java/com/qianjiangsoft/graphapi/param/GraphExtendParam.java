package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.constants.ExtendDirection;
import com.qianjiangsoft.graphcore.param.Condition;
import com.qianjiangsoft.graphcore.param.SortingParam;
import io.swagger.models.auth.In;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/4/8
 */
@Data
public class GraphExtendParam {
    //起点ID
    private List<String> startVertexIdList;

    //边类型Id
    private List<String> edgeTypeNameList;

    //最小步数
    private Integer minStep;

    //最大步数
    private Integer maxStep;

    //扩展条件
    private ExtendDirection extendDirection;

    //时间属性
    private Condition timeCondition;

    //强度属性
    private Condition strengthCondition;

    //次数属性
    private Condition countCondition;

    //排序
    private SortingParam sorting;

}
