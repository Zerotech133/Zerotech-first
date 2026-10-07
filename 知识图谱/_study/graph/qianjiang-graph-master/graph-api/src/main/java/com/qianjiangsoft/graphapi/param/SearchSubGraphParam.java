package com.qianjiangsoft.graphapi.param;


import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/4/8
 */
@Data
public class SearchSubGraphParam {
    //点列表
    private List<String> vertexIdList;
    //步数
    private long step;
    //类型
    private List<FindSubGraphParam> edgeParamList;

    private String taskName;

}
