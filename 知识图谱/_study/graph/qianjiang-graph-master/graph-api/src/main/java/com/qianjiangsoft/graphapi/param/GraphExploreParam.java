package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.constants.SearchPathDirection;
import com.qianjiangsoft.graphcore.constants.SearchPathType;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/4/19
 */
@Data
public class GraphExploreParam {
    //起点
    private VertexParam startVertexId;

    //终点
    private VertexParam endVertexId;

    //边类型
    private List<String> edgeTypeNameList;

    //步数
    private Integer maxStep;

    //搜索类型
    private SearchPathType searchPathType;


    private SearchPathDirection direction;

    //结果数量
    private int total;

    private String taskName;
}
