package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.collection.CollUtil;
import com.qianjiangsoft.graphapi.dto.GraphCommonReturnDTO;
import com.qianjiangsoft.graphapi.helper.GraphAnalyseHelper;
import com.qianjiangsoft.graphapi.param.custom.QueryCrossCaseUnknownSuspectParam;
import com.qianjiangsoft.graphapi.service.AnalysisCustomService;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.helper.VertexHelper;
import com.qianjiangsoft.graphcore.model.MergeEdge;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.model.Vertex;
import com.qianjiangsoft.graphcore.model.VertexPropertyCaseInfo;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;


@Service
public class AnalysisCustomServiceImpl implements AnalysisCustomService {


    @Override
    public GraphCommonReturnDTO queryCrossCaseUnknownSuspect() {
        Space space = SpaceContextHolder.get();

        //获取所有点
        List<Vertex> vertexList = space.getVertexList();

        //过滤只少包含两个案件信息的点
        List<Vertex> filterVertexList = vertexList.stream()
                .filter(vertex -> (VertexHelper.getCaseInfoListFromProperty(vertex.getPropertyList()).size() > 1)
                        && !GraphAnalyseHelper.checkSuspectFlag(vertex.getPropertyList()))
                .limit(1000).collect(Collectors.toList());

        List<MergeEdge> mergeEdgeList = new ArrayList<>();

        //遍历点，查询案件信息
        filterVertexList.forEach(vertex -> {

            //扩展一层点(正向)
            Set<MergeEdge> forwardEdgeSet = space.getMergeEdgeAdjacencyTableMap().get(vertex);
            if(forwardEdgeSet != null) {
                List<MergeEdge> forwardEdgeList = forwardEdgeSet.stream()
                        .filter(mergeEdge -> GraphAnalyseHelper.checkSuspectFlag(mergeEdge.getDestination().getPropertyList()))
                        .collect(Collectors.toList());
                CollUtil.addAll(mergeEdgeList, forwardEdgeList);
            }

            //扩展一层点(逆向)
            Set<MergeEdge> backwardEdgeSet = space.getReverseMergeEdgeAdjacencyTableMap().get(vertex);
            if(backwardEdgeSet != null) {
                List<MergeEdge> backwardEdgeList = backwardEdgeSet.stream()
                        .filter(mergeEdge -> GraphAnalyseHelper.checkSuspectFlag(mergeEdge.getDestination().getPropertyList()))
                        .collect(Collectors.toList());
                CollUtil.addAll(mergeEdgeList, backwardEdgeList);
            }
        });

        return GraphAnalyseHelper.convertMergeEdgeToGraphCommonReturnDTO(null, mergeEdgeList);
    }
}
